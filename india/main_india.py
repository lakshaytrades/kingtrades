"""
main_india.py -- KingTrades India Bot
Dhan broker | NSE equity | IST timezone | INR capital

Architecture: parallel to US bot -- zero shared state.
Reuses: pattern_recognition, high_accuracy_filter, neural_predictor,
        volatility_targeting, institutional_strategies (IST-adapted)
India-specific: auth_dhan, data_fetch_dhan, execution_dhan,
                watchlist_india, signal_generator_india
"""
import json
import logging
import os
import signal
import sys
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from zoneinfo import ZoneInfo

# -- Path setup (import parent modules) ---------------------------------------
_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(Path(__file__).parent))

# -- Load env from .env (parent directory) ------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except ImportError:
    pass

# -- Local imports ------------------------------------------------------------
import config_india as config
from auth_dhan              import get_dhan_client, verify_connection
from data_fetch_dhan        import get_ohlcv, get_multiple_ltp
from execution_dhan         import DhanExecutor, get_executor
from watchlist_india        import get_active_watchlist, get_sector
from signal_generator_india import IndiaSignalGenerator, IndiaTradeSignal

# -- Logging ------------------------------------------------------------------
LOG_DIR = _BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

IST = ZoneInfo("Asia/Kolkata")

# Server runs in UTC. Make %(asctime)s emit IST so the "[IST]" label is truthful
# and logged times match the bot's trading clock (datetime.now(IST)).
# This is a process-wide class attribute, so it fixes every logger in this process.
def _ist_log_converter(*args):
    # Assigned as a class attribute, so it may be called bound (self, ts) or
    # unbound (ts); the epoch timestamp is always the last positional arg.
    return datetime.fromtimestamp(args[-1], IST).timetuple()
logging.Formatter.converter = staticmethod(_ist_log_converter)

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [IST] [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / f"india_{datetime.now(IST).strftime('%Y-%m-%d')}.log"),
    ],
    force   = True,   # override any root handler an imported lib configured first
)
logger = logging.getLogger("main_india")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)


# -- Telegram helper ----------------------------------------------------------

def _tg(msg: str, parse_mode: str = "Markdown"):
    try:
        token = config.TELEGRAM_BOT_TOKEN
        chat  = config.TELEGRAM_CHAT_ID
        if not token or not chat:
            logger.info(f"[TG] {msg[:120]}")
            return
        # Multi-chat: TELEGRAM_CHAT_ID may be a comma/space-separated list to
        # broadcast to any number of chats (e.g. "12345,-100999,67890").
        chat_ids = [c.strip() for c in str(chat).replace(" ", ",").split(",") if c.strip()]
        import requests
        for cid in chat_ids:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": cid, "text": msg, "parse_mode": parse_mode},
                    timeout=8,
                )
            except Exception as _ce:
                logger.debug(f"telegram chat {cid}: {_ce}")
    except Exception as e:
        logger.debug(f"telegram: {e}")


def _tg_verbose(msg: str, parse_mode: str = "Markdown"):
    """
    Non-essential notification. Silent by default so Telegram shows ONLY:
    (1) trade to take / taken, (2) command replies, (3) bot-start message.
    Set INDIA_TELEGRAM_VERBOSE=True to re-enable all the operational chatter.
    """
    if getattr(config, "TELEGRAM_VERBOSE", False):
        _tg(msg, parse_mode)
    else:
        logger.info(f"[quiet] {msg[:120]}")


# -- Open position tracking ---------------------------------------------------

@dataclass
class OpenPosition:
    symbol:       str
    direction:    str
    entry_price:  float
    stop_loss:    float
    target_1:     float
    target_2:     float
    quantity:     int
    security_id:  str
    entry_time:   datetime = field(default_factory=lambda: datetime.now(IST))
    stop_order_id: str     = ""
    breakeven_moved: bool  = False
    atr:          float    = 0.0
    t1_exited:     bool  = False
    is_scalp:      bool  = False
    time_stop_min: int   = 0
    signal_contributions: dict = field(default_factory=dict)


# -- Daily stats --------------------------------------------------------------

@dataclass
class DayStats:
    trades:             int   = 0
    wins:               int   = 0
    losses:             int   = 0
    total_pnl:          float = 0.0
    daily_start:        float = 0.0
    circuit_hit:        bool  = False
    consecutive_losses: int   = 0   # resets on each win
    loss_guard_active:  bool  = False  # raised after 3 consecutive losses


# -- Main bot -----------------------------------------------------------------

class KingTradesIndia:

    def __init__(self):
        self._dhan      = None
        self._executor  = None
        self._generator = None
        self._watchlist : List[str]             = []
        self._positions : Dict[str, OpenPosition] = {}
        self._shadow_positions : Dict[str, dict] = {}   # manual-mode signal tracking
        self._scanned   : set                   = set()
        self._stats     = DayStats()
        self._running   = True
        self._last_trade_ts: float = 0.0
        self._daily_target_pct: float = 0.005  # 0.5%

    # -- Startup ---------------------------------------------------------------

    def initialize(self) -> bool:
        logger.info("=" * 60)
        logger.info(f"KingTrades India Bot starting -- {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
        logger.info(f"Live trading: {config.LIVE_TRADING_ENABLED}")
        logger.info(f"Capital: Rs.{config.MAX_DAILY_CAPITAL:,.0f} | Max positions: {config.MAX_POSITIONS}")

        # Dhan connection
        self._dhan = get_dhan_client()
        if not verify_connection(self._dhan):
            if config.LIVE_TRADING_ENABLED:
                logger.error("Dhan connection failed -- cannot start in live mode")
                _tg_verbose("India Bot failed to connect to Dhan -- check DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN")
                return False
            logger.warning("Dhan not connected -- running in paper mode")

        # Register client with data module so OHLCV/VIX/Nifty use Dhan API
        import data_fetch_dhan as _dfd
        _dfd.set_dhan_client(self._dhan)

        self._executor = get_executor(self._dhan, config.LIVE_TRADING_ENABLED)

        # Watchlist
        self._watchlist = get_active_watchlist(self._dhan)
        logger.info(f"Watchlist: {len(self._watchlist)} symbols")

        # Signal generator
        self._generator = IndiaSignalGenerator(config, self._watchlist)

        # Pre-load data source: warm the Upstox instrument map (if token set) and
        # confirm the active feed so the bot is "fully data loaded" at startup.
        self._data_source = "Yahoo (free, ~15m delayed)"
        try:
            import upstox_data as _ux
            if _ux.enabled():
                _ux._load_instruments()                 # warm symbol->key map
                if _ux._sym_to_key:
                    self._data_source = f"Upstox (real-time, {len(_ux._sym_to_key)} instruments)"
                    logger.info(f"Upstox active: {len(_ux._sym_to_key)} instruments loaded")
                else:
                    logger.warning("Upstox token set but instruments failed to load — using Yahoo")
        except Exception as _ue:
            logger.debug(f"upstox preload: {_ue}")
        logger.info(f"Data source: {self._data_source}")

        # Balance
        balance = self._get_balance()
        self._stats.daily_start = balance
        logger.info(f"Available balance: Rs.{balance:,.2f}")

        # India VIX circuit breaker
        if getattr(config, 'INDIA_VIX_ENABLED', True):
            try:
                from data_fetch_dhan import get_india_vix as _get_vix
                _vix = _get_vix()
                if _vix > 0:
                    logger.info(f"India VIX: {_vix:.1f}")
                    if _vix >= getattr(config, 'INDIA_VIX_EXTREME_THRESHOLD', 28.0):
                        self._stats.circuit_hit = True
                        _tg_verbose(f"India VIX {_vix:.1f} EXTREME -- no new trades today")
                    elif _vix >= getattr(config, 'INDIA_VIX_HIGH_THRESHOLD', 22.0):
                        _tg_verbose(f"India VIX {_vix:.1f} HIGH -- sizes reduced")
            except Exception:
                pass

        # Start Telegram command listener
        self._start_telegram_listener()
        self._write_state_file()   # write immediately so /status shows India bot is online

        mode = "LIVE TRADING" if config.LIVE_TRADING_ENABLED else "PAPER MODE"
        _tg(
            f"INDIA BOT -- Session Started\n"
            f"--------------------------------\n"
            f"{datetime.now(IST).strftime('%d %b %Y, %H:%M IST')}\n"
            f"Mode: {mode}\n"
            f"Capital: Rs.{config.MAX_DAILY_CAPITAL:,.0f} | Max pos: {config.MAX_POSITIONS}\n"
            f"Watchlist: {len(self._watchlist)} NSE symbols\n"
            f"Engine: 12 sources | 26 gates | ML-scored | ORB\n"
            f"Broker: Dhan | Market: NSE | Opens 9:15 AM IST\n"
            f"--------------------------------"
        )
        # If today is 1st of month, auto-send last month's P&L summary
        try:
            from trade_journal_india import maybe_send_monthly_summary
            maybe_send_monthly_summary(_tg_verbose)
        except Exception:
            pass
        # On Mondays, auto-send the 90-day forward-test GO/NO-GO proof report
        try:
            from trade_journal_india import maybe_send_weekly_forward_test
            maybe_send_weekly_forward_test(_tg_verbose)
        except Exception:
            pass
        return True

    # -- Main loop -------------------------------------------------------------

    def run(self):
        if not self.initialize():
            return

        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT,  self._handle_shutdown)

        while self._running:
            try:
                now = datetime.now(IST)
                t   = now.time()

                # -- Pre-market: wait -----------------------------------------
                if t < config.PRE_MARKET_START_IST:
                    self._write_state_file()
                    _time.sleep(60)
                    continue

                # -- Square-off warning ---------------------------------------
                if config.SQUAREOFF_WARN_IST <= t < config.SQUAREOFF_TIME_IST:
                    if self._positions:
                        syms = ", ".join(self._positions.keys())
                        _tg_verbose(f"INDIA BOT Square-off Warning\n"
                            f"3:15 PM IST -- {len(self._positions)} open: {syms}\n"
                            f"Auto-closing at 3:20 PM IST")

                # -- Force square-off -----------------------------------------
                if t >= config.SQUAREOFF_TIME_IST:
                    self._force_square_off()
                    self._square_off_shadows()   # journal open manual-mode signals
                    self._send_eod_report()
                    logger.info("Square-off complete. Shutting down for the day.")
                    break

                # -- Market hours: scan + manage -------------------------------
                if config.MARKET_OPEN_IST <= t < config.SQUAREOFF_TIME_IST:
                    if not self._stats.circuit_hit:
                        self._scan_for_signals(t)
                    self._manage_positions()
                    self._manage_shadow_positions()   # track manual-mode signal edge
                    self._write_state_file()

                # -- Adaptive scan interval ------------------------------------
                # Faster: 30s during high-volume windows (open + power close),
                # 120s otherwise. Parallel scanning keeps this cheap.
                _in_power = (time(9, 15) <= t <= time(9, 59)) or \
                            (time(14, 30) <= t <= time(15, 20))
                _fast = getattr(config, "FAST_SCAN_SECONDS", 30)
                _time.sleep(_fast if _in_power else config.SCAN_INTERVAL_SECONDS)

            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                _time.sleep(30)

    # -- Signal scan ----------------------------------------------------------

    def _nifty_regime(self) -> str:
        """
        Check Nifty50 15m EMA trend. Returns 'BULLISH', 'BEARISH', or 'NEUTRAL'.
        Cached 15 minutes -- avoids repeated downloads in the scan loop.
        BULLISH -> only LONG signals allowed.
        BEARISH -> only SHORT signals allowed.
        NEUTRAL -> both directions allowed (ORB works on flat days too).
        Fail-open: returns 'NEUTRAL' on any error.
        """
        try:
            now_ts = _time.time()
            if not hasattr(self, "_nifty_cache"):
                self._nifty_cache: dict = {}
            if self._nifty_cache.get("ts", 0) > now_ts - 900:
                return self._nifty_cache.get("regime", "NEUTRAL")

            from data_fetch_dhan import get_nifty_intraday as _get_nifty
            df = _get_nifty(interval="15m")
            if df is None or df.empty or len(df) < 21:
                return "NEUTRAL"

            ema9  = float(df["close"].ewm(span=9,  adjust=False).mean().iloc[-1])
            ema21 = float(df["close"].ewm(span=21, adjust=False).mean().iloc[-1])
            gap   = (ema9 - ema21) / (ema21 + 1e-9)

            # < 0.3% gap: EMA nearly flat -> neutral, allow both directions
            # Old 0.05% threshold was too tight — blocked all longs at -0.1% gaps
            if abs(gap) < 0.003:
                regime = "NEUTRAL"
            elif gap > 0:
                regime = "BULLISH"
            else:
                regime = "BEARISH"

            self._nifty_cache = {"ts": now_ts, "regime": regime}
            logger.info(f"Nifty regime: {regime} (EMA9={ema9:.0f} EMA21={ema21:.0f} gap={gap:.3%})")
            return regime
        except Exception as e:
            logger.debug(f"nifty_regime: {e}")
            return "NEUTRAL"

    def _scan_for_signals(self, current_time: Optional[time] = None):
        if len(self._positions) >= config.MAX_POSITIONS:
            return

        # -- Supervisor halt: capital-protection layer stops new trades -------
        # supervisor_india.py writes this flag when a hard loss limit is hit.
        try:
            from pathlib import Path as _P
            if _P("/tmp/india_halt.flag").exists():
                if not self._stats.circuit_hit:
                    self._stats.circuit_hit = True
                    logger.warning("Supervisor HALT flag set — no new trades today")
                return
        except Exception:
            pass

        # -- Idle scalp state -------------------------------------------------
        _now_mono = _time.monotonic()
        _idle_min = (_now_mono - self._last_trade_ts) / 60 if self._last_trade_ts > 0 else 999
        _pnl_pct  = self._stats.total_pnl / max(config.MAX_DAILY_CAPITAL, 1)
        _scalp    = (getattr(config, 'IDLE_SCALP_ENABLED', True) and
                     _idle_min >= getattr(config, 'IDLE_SCALP_THRESHOLD_MIN', 30) and
                     _pnl_pct  <  getattr(config, 'DAILY_TARGET_PCT', 0.005) * 0.7)
        if self._generator:
            self._generator._session_wins   = self._stats.wins
            self._generator._session_losses = self._stats.losses
            self._generator._idle_scalp_active = _scalp

        # -- Scan diagnostics: track rejection reasons -------------------------
        if not hasattr(self, "_scan_stats"):
            self._scan_stats = {"scanned": 0, "signals": 0, "rejected_no_sig": 0,
                                "rejected_regime": 0, "rejected_orb": 0,
                                "rejected_qty": 0, "last_tg_ts": 0.0,
                                "data_ok": 0, "data_fail": 0, "data_warn_ts": 0.0}
        _ss = self._scan_stats
        _ss["scanned"] = 0   # reset per scan cycle

        # -- NSE OHLCV data health check (3-symbol sample every scan) ---------
        # Parallel: 3 concurrent fetches instead of blocking sequentially.
        # Warms the OHLCV cache too, so the scan below reuses these candles.
        _sample_syms = self._watchlist[:3]

        def _check_one(_dsym):
            try:
                from data_fetch_dhan import get_ohlcv as _gohlcv
                _df = _gohlcv(_dsym, interval="5m", period="5d")
                return _df is not None and not _df.empty and len(_df) >= 20
            except Exception:
                return False

        _data_ok_count = 0
        if _sample_syms:
            with ThreadPoolExecutor(max_workers=len(_sample_syms)) as _hpool:
                _data_ok_count = sum(_hpool.map(_check_one, _sample_syms))
        _ss["data_ok"]   = _data_ok_count
        _ss["data_fail"] = len(_sample_syms) - _data_ok_count

        if _data_ok_count == 0 and len(_sample_syms) > 0:
            logger.warning("NSE OHLCV data: 0/3 sample symbols loaded — NSE charting API may be down")
            if _time.monotonic() - _ss["data_warn_ts"] > 1800:  # alert max every 30 min
                _ss["data_warn_ts"] = _time.monotonic()
                _tg_verbose(
                    "⚠️ INDIA BOT — NSE Data Alert\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "NSE charting API returning NO data for 3 sample symbols.\n"
                    "This is why 0 signals are found — no OHLCV candles to analyse.\n\n"
                    "Fix on VPS:\n"
                    "curl -s 'https://www.nseindia.com/api/allIndices' | head -100\n"
                    "# If above works but signals still 0 → NSE charting blocked.\n"
                    "# Check VPS IP is Indian and not rate-limited by NSE."
                )

        # ORB-only window: 9:15-9:30 IST -- market still settling.
        _t = current_time or datetime.now(IST).time()
        _orb_only = time(9, 15) <= _t < time(9, 30)

        # Loss guard: 3 consecutive losses today -> skip signals
        if self._stats.loss_guard_active and not self._stats.circuit_hit:
            logger.info("Loss guard active -- pausing new entries")
            return

        # Nifty regime: don't trade LONGs in bearish market, SHORTs in bullish market
        regime = self._nifty_regime()

        ltp_map = {}
        try:
            ltp_map = get_multiple_ltp(self._watchlist, self._dhan)
        except Exception:
            pass

        # -- Parallel signal generation (CPU-bound analysis) -------------------
        # generate_signal() reads data but does NOT write shared state, so it
        # is safe to run in parallel. Order dispatch (buy/sell) stays sequential.
        _candidates_to_scan = [
            sym for sym in self._watchlist
            if sym not in self._positions
        ]

        def _gen_signal_safe(sym):
            try:
                ltp = ltp_map.get(sym, 0.0)
                return sym, self._generator.generate_signal(sym, current_price=ltp)
            except Exception as _e:
                logger.error(f"Signal scan {sym}: {_e}")
                return sym, None

        _max_workers = min(8, len(_candidates_to_scan)) if _candidates_to_scan else 1
        _signal_results: list = []
        with ThreadPoolExecutor(max_workers=_max_workers) as _pool:
            _futures = {_pool.submit(_gen_signal_safe, sym): sym
                        for sym in _candidates_to_scan}
            for _fut in as_completed(_futures):
                _signal_results.append(_fut.result())

        # Sort by signal score descending so strongest trades go first
        _signal_results.sort(
            key=lambda x: getattr(x[1], "signal_score", 0) if x[1] else 0,
            reverse=True,
        )

        for symbol, signal_obj in _signal_results:
            if len(self._positions) >= config.MAX_POSITIONS:
                break
            if self._stats.circuit_hit:
                break

            _ss["scanned"] += 1

            if signal_obj is None:
                _ss["rejected_no_sig"] += 1
                continue

            # ORB-only: skip non-ORB signals during 9:15-9:30 IST
            if _orb_only:
                has_orb = any("orb" in str(p).lower()
                              for p in getattr(signal_obj, "patterns", []))
                if not has_orb:
                    logger.info(f"{symbol}: skipped (ORB-only window 9:15-9:30)")
                    _ss["rejected_orb"] += 1
                    continue

            # Nifty regime filter: don't fight the market
            if regime == "BEARISH" and signal_obj.direction == "LONG":
                logger.info(f"{symbol}: LONG skipped (Nifty BEARISH regime)")
                _ss["rejected_regime"] += 1
                continue
            if regime == "BULLISH" and signal_obj.direction == "SHORT":
                logger.info(f"{symbol}: SHORT skipped (Nifty BULLISH regime)")
                _ss["rejected_regime"] += 1
                continue

            _ss["signals"] += 1
            logger.info(
                f"✅ SIGNAL: {symbol} {signal_obj.direction} "
                f"score={signal_obj.signal_score:.1f} "
                f"grade={signal_obj.quality_grade} "
                f"entry=Rs.{signal_obj.entry_price:.2f} "
                f"sl=Rs.{signal_obj.stop_loss:.2f} "
                f"tp=Rs.{signal_obj.target_1:.2f}"
            )

            # Position sizing
            qty = self._calculate_qty(signal_obj)
            if qty <= 0:
                logger.info(f"{symbol}: qty=0 -- capital Rs.{config.MAX_DAILY_CAPITAL:,.0f} insufficient")
                _ss["rejected_qty"] += 1
                continue

            # ── MANUAL SIGNALS MODE: alert Telegram, user places in Dhan ──
            if getattr(config, 'MANUAL_SIGNALS_ONLY', True):
                sent = self._send_manual_signal_alert(signal_obj, qty)
                self._last_trade_ts = _time.monotonic()
                # Shadow-track the signal so /proof, /monthly, /weekly measure
                # the bot's REAL edge even though we place no order ourselves.
                if sent and symbol not in self._shadow_positions:
                    self._shadow_positions[symbol] = {
                        "direction": signal_obj.direction,
                        "entry":     signal_obj.entry_price,
                        "sl":        signal_obj.stop_loss,
                        "t1":        signal_obj.target_1,
                        "t2":        signal_obj.target_2,
                        "qty":       qty,
                        "score":     getattr(signal_obj, "signal_score", 0.0),
                        "grade":     getattr(signal_obj, "quality_grade", ""),
                        "ts":        _time.monotonic(),
                    }
                continue

            # ── AUTO-EXECUTION (MANUAL_SIGNALS_ONLY=False in .env) ────────
            # Guard: security_id required for LIVE orders; paper mode uses fallback
            if not signal_obj.security_id:
                if config.LIVE_TRADING_ENABLED:
                    logger.warning(f"{symbol}: no security_id -- skipping live order")
                    _ss["rejected_qty"] += 1
                    continue
                else:
                    signal_obj.security_id = f"PAPER-{symbol}"

            result = self._executor.place_entry_order(
                symbol=symbol, direction=signal_obj.direction,
                qty=qty, price=signal_obj.entry_price,
                security_id=signal_obj.security_id,
            )
            if not result.success:
                logger.warning(f"{symbol}: entry failed -- {result.message}")
                continue

            fill_price = result.fill_price or signal_obj.entry_price
            fill_qty   = result.quantity or qty
            if fill_qty <= 0 or fill_price <= 0:
                logger.warning(f"{symbol}: fill not confirmed -- skipping")
                continue

            stop_result = self._executor.place_stop_order(
                symbol=symbol, direction=signal_obj.direction,
                qty=fill_qty, stop_price=signal_obj.stop_loss,
                security_id=signal_obj.security_id,
            )
            if not stop_result.success:
                logger.error(f"{symbol}: stop FAILED -- reversing entry")
                _tg_verbose(f"INDIA BOT Stop failed for {symbol} -- reversing entry")
                try:
                    self._executor.square_off_all(self._executor.get_open_positions())
                except Exception as _rev_e:
                    # Reversal itself failed -> we may hold a NAKED unhedged
                    # position. This needs a human NOW.
                    logger.error(f"{symbol}: REVERSAL FAILED -- naked position! {_rev_e}",
                                 exc_info=True)
                    _tg_verbose(f"🚨 INDIA BOT URGENT: {symbol} has NO STOP and reversal "
                        f"FAILED. You may hold an unhedged position — square it "
                        f"off MANUALLY in Dhan NOW. ({_rev_e})")
                continue

            pos = OpenPosition(
                symbol=symbol, direction=signal_obj.direction,
                entry_price=fill_price, stop_loss=signal_obj.stop_loss,
                target_1=signal_obj.target_1, target_2=signal_obj.target_2,
                quantity=int(fill_qty), security_id=signal_obj.security_id,
                stop_order_id=stop_result.order_id, atr=signal_obj.atr,
                is_scalp=getattr(signal_obj, 'is_scalp', False),
                time_stop_min=getattr(signal_obj, 'time_stop_min', 0),
            )
            self._positions[symbol] = pos
            self._stats.trades += 1
            self._last_trade_ts = _time.monotonic()
            _tg(
                f"INDIA BOT Auto-Executed -- {symbol}\n"
                f"Entry: Rs.{fill_price:.2f} | Qty: {fill_qty}\n"
                f"SL: Rs.{signal_obj.stop_loss:.2f} | T1: Rs.{signal_obj.target_1:.2f}\n"
                f"Score: {signal_obj.signal_score:.0f} | Grade: {signal_obj.quality_grade}"
            )
            try:
                from daily_intelligence_india import explain_trade as _explain
                signal_obj.quantity = int(fill_qty)
                _tg(_explain(signal_obj))
            except Exception:
                pass
            self._write_state_file()

        # -- Scan summary: log always, Telegram every 30 min ------------------
        _total = len(self._watchlist)
        _no_sig = _ss["rejected_no_sig"]
        _regime = _ss["rejected_regime"]
        _orb_r  = _ss["rejected_orb"]
        _qty_r  = _ss["rejected_qty"]
        _sigs   = _ss["signals"]
        _dok    = _ss.get("data_ok", 0)
        _dfail  = _ss.get("data_fail", 0)
        _data_tag = f"✅ {_dok}/3" if _dok > 0 else "❌ 0/3 — NSE data DOWN"
        logger.info(
            f"[SCAN] {_total} symbols | signals={_sigs} | "
            f"no_signal={_no_sig} | regime_filter={_regime} | "
            f"orb_only={_orb_r} | qty_fail={_qty_r} | "
            f"data={_dok}/3 | regime={regime} | scalp={'ON' if _scalp else 'off'}"
        )
        _now_ts = _time.monotonic()
        if (_now_ts - _ss.get("last_tg_ts", 0) > 1800
                and getattr(config, "TELEGRAM_VERBOSE", False)):   # scan pulse: verbose only
            _ss["last_tg_ts"] = _now_ts
            _now_ist = datetime.now(IST)
            _mode_tag = "LIVE" if config.LIVE_TRADING_ENABLED else "PAPER"
            _tg(
                f"📊 INDIA BOT — Scan Pulse ({_now_ist.strftime('%H:%M IST')})\n"
                f"Mode: {_mode_tag} | Regime: {regime} | Scalp: {'ON' if _scalp else 'off'}\n"
                f"NSE Data: {_data_tag}\n"
                f"Scanned: {_total} | Signals found: {_sigs}\n"
                f"Rejected → no signal: {_no_sig} | regime: {_regime} | ORB window: {_orb_r} | qty: {_qty_r}\n"
                f"Day P&L: Rs.{self._stats.total_pnl:+,.0f} | Trades: {self._stats.trades} | "
                f"Pos: {len(self._positions)}/{config.MAX_POSITIONS}"
            )

    # -- On-demand setups (/today) --------------------------------------------

    def _build_today_setups(self) -> str:
        """
        Scan the watchlist on demand and return the current A-grade setups for
        manual trading. Shows passing signals; if none, the top candidates by score.
        """
        if not self._generator:
            return "Bot not initialised yet."
        passed, candidates = [], []
        ltp_map = {}
        try:
            ltp_map = get_multiple_ltp(self._watchlist, self._dhan)
        except Exception:
            pass
        for sym in self._watchlist[:30]:   # cap for responsiveness
            try:
                ltp = ltp_map.get(sym, 0.0)
                sig = self._generator.generate_signal(sym, current_price=ltp)
                if sig is not None:
                    passed.append(sig)
                else:
                    sc = getattr(self._generator, "_last_score", 0) or 0
                    if sc:
                        candidates.append((sym, sc))
            except Exception:
                continue
        now = datetime.now(IST).strftime("%d %b %H:%M IST")
        if passed:
            passed.sort(key=lambda s: getattr(s, "signal_score", 0), reverse=True)
            lines = [f"🇮🇳 TODAY'S SETUPS — {now}", "━━━━━━━━━━━━━━━━━━━━"]
            for s in passed[:5]:
                lines.append(
                    f"{'🟢 BUY' if s.direction=='LONG' else '🔴 SELL'} {s.symbol} "
                    f"| {getattr(s,'quality_grade','?')} {getattr(s,'signal_score',0):.0f}\n"
                    f"  Entry ₹{s.entry_price:.2f} | SL ₹{s.stop_loss:.2f} | "
                    f"T1 ₹{s.target_1:.2f}"
                )
            lines.append("\nPlace these manually in your broker.")
            return "\n".join(lines)
        msg = f"🇮🇳 No A-grade setups right now — {now}\nBot is waiting for quality (high-accuracy mode)."
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            near = " | ".join(f"{s} {sc:.0f}" for s, sc in candidates[:5])
            msg += f"\nClosest: {near}  (need ≥{config.FINAL_EXEC_MIN_SCORE:.0f})"
        return msg

    # -- Data source status (/data) -------------------------------------------

    def _build_data_status(self) -> str:
        """Show which feed is active and prove data is loading (live sample)."""
        lines = ["📡 DATA SOURCE STATUS", "━" * 26]
        src = getattr(self, "_data_source", "unknown")
        lines.append(f"Active: {src}")
        # Upstox detail
        try:
            import upstox_data as _ux
            if _ux.enabled():
                lines.append(f"Upstox token: ✅ set | instruments: {len(_ux._sym_to_key)}")
            else:
                lines.append("Upstox token: ❌ not set (using free Yahoo)")
        except Exception:
            pass
        # Live sample — proves data actually flows right now
        syms = self._watchlist[:5]
        try:
            ltp = get_multiple_ltp(syms, self._dhan)
        except Exception:
            ltp = {}
        lines.append("━" * 26)
        lines.append("Live sample (proves data is loading):")
        ok = 0
        for s in syms:
            px = ltp.get(s, 0)
            if px > 0:
                ok += 1
                lines.append(f"  {s}: ₹{px:,.2f}")
            else:
                lines.append(f"  {s}: — no data")
        lines.append("━" * 26)
        lines.append(f"Loaded {ok}/{len(syms)} sample symbols "
                     f"{'✅ healthy' if ok == len(syms) else ('⚠️ partial' if ok else '❌ DOWN')}")
        lines.append(f"Universe: {len(self._watchlist)} stocks | scan every "
                     f"{getattr(config,'FAST_SCAN_SECONDS',30)}-{config.SCAN_INTERVAL_SECONDS}s")
        return "\n".join(lines)

    # -- Manual signal alert --------------------------------------------------

    def _send_manual_signal_alert(self, sig, qty: int) -> bool:
        """
        Send a rich Telegram alert with everything needed to place the order
        manually in Dhan app. Called instead of auto-execution when
        MANUAL_SIGNALS_ONLY=True. Returns True if an alert was actually sent,
        False if suppressed by the dedup window.
        """
        # Dedup: don't alert the same symbol twice within 10 minutes
        if not hasattr(self, "_alerted_symbols"):
            self._alerted_symbols: dict = {}
        last_alert = self._alerted_symbols.get(sig.symbol, 0)
        if _time.monotonic() - last_alert < 600:
            logger.info(f"{sig.symbol}: signal alert suppressed (sent <10 min ago)")
            return False
        self._alerted_symbols[sig.symbol] = _time.monotonic()

        symbol    = sig.symbol
        direction = sig.direction
        entry     = sig.entry_price
        sl        = sig.stop_loss
        t1        = sig.target_1
        t2        = sig.target_2
        score     = sig.signal_score
        grade     = sig.quality_grade
        atr       = sig.atr
        rationale = getattr(sig, "rationale", "")
        patterns  = getattr(sig, "patterns", [])
        is_scalp  = getattr(sig, "is_scalp", False)

        arrow = "↑ BUY" if direction == "LONG" else "↓ SELL"
        sl_pct = abs(entry - sl) / entry * 100
        t1_pct = abs(t1 - entry) / entry * 100
        t2_pct = abs(t2 - entry) / entry * 100
        sl_dist = abs(entry - sl)
        rr = abs(t1 - entry) / sl_dist if sl_dist > 0 else 0
        invested = qty * entry
        risk_inr = qty * sl_dist
        sector = get_sector(symbol)
        now_ist = datetime.now(IST)
        time_str = now_ist.strftime("%H:%M IST")

        # Grade emoji
        grade_icon = {"A+": "🏆", "A": "✅", "B+": "🟡", "B": "🟠", "C": "⚠️"}.get(grade, "")
        trade_type = "⚡ SCALP" if is_scalp else "📊 INTRADAY"
        urgency    = "Act within 5 min" if is_scalp else "Act within 10 min"

        # Setup reasons from rationale
        reasons = []
        if patterns:
            reasons.append(", ".join(str(p) for p in patterns[:3]))
        if rationale:
            parts_r = rationale.split("|")
            for pr in parts_r:
                pr = pr.strip()
                if pr and not pr.startswith("Score") and not pr.startswith("Grade") and "RR" not in pr:
                    reasons.append(pr)
        setup_str = " + ".join(reasons[:2]) if reasons else rationale[:60]

        # FII/VIX context
        fii_str = ""
        try:
            from fii_dii_india import get_fii_dii_bias as _fii
            bias, _, reason = _fii()
            fii_str = f"FII: {bias}"
        except Exception:
            pass

        vix_str = ""
        try:
            from data_fetch_dhan import get_india_vix as _vix
            vix = _vix()
            if vix > 0:
                vix_tag = "🔴HIGH" if vix >= 22 else ("🟡" if vix >= 17 else "🟢")
                vix_str = f"VIX: {vix:.1f} {vix_tag}"
        except Exception:
            pass

        context_parts = [p for p in [vix_str, fii_str, f"Sector: {sector}"] if p]
        context_str = "  |  ".join(context_parts)

        # Dhan order instruction
        if direction == "LONG":
            dhan_action = "BUY"
            dhan_order = f"BUY {symbol} | MIS | Market | {qty} qty"
            sl_note = f"After filling: place SL order SELL {symbol} SL-M trigger Rs.{sl:.2f}"
        else:
            dhan_action = "SELL"
            dhan_order = f"SELL {symbol} | MIS | Market | {qty} qty"
            sl_note = f"After filling: place SL order BUY {symbol} SL-M trigger Rs.{sl:.2f}"

        sep = "━" * 28
        msg = (
            f"{trade_type}  {grade_icon} Grade {grade}  |  Score {score:.0f}/100\n"
            f"{sep}\n"
            f"{arrow}  NSE:{symbol}\n"
            f"{sep}\n"
            f"Entry:  Rs.{entry:,.2f}  (market)\n"
            f"Stop:   Rs.{sl:,.2f}  ({sl_pct:.1f}% away) ← SET THIS IN DHAN\n"
            f"T1:     Rs.{t1:,.2f}  (+{t1_pct:.1f}%) ← exit 50%\n"
            f"T2:     Rs.{t2:,.2f}  (+{t2_pct:.1f}%) ← trail rest\n"
            f"R:R     {rr:.1f} : 1\n"
            f"{sep}\n"
            f"Setup:  {setup_str}\n"
            f"{context_str}\n"
            f"Time:   {time_str}  |  {urgency}\n"
            f"{sep}\n"
            f"Qty:    {qty} shares  ≈  Rs.{invested:,.0f} invested\n"
            f"Risk:   Rs.{risk_inr:,.0f}  ({risk_inr/max(config.MAX_DAILY_CAPITAL,1)*100:.2f}% of capital)\n"
            f"{sep}\n"
            f"📈 tradingview.com/chart/?symbol=NSE:{symbol}\n"
            f"🏦 Dhan: {dhan_order}\n"
            f"🛡 {sl_note}"
        )

        _tg(msg)
        logger.info(f"[MANUAL ALERT] {direction} {symbol} score={score:.0f} qty={qty} sent to Telegram")
        return True

    # -- Shadow tracking (manual mode edge measurement) -----------------------

    def _journal_shadow(self, sym: str, sp: dict, exit_price: float) -> None:
        """Journal a shadow (manual-mode) signal outcome for the proof reports."""
        if sp["direction"] == "LONG":
            pnl = (exit_price - sp["entry"]) * sp["qty"]
        else:
            pnl = (sp["entry"] - exit_price) * sp["qty"]
        try:
            from trade_journal_india import record_trade as _jrec
            _jrec(symbol=sym, direction=sp["direction"], entry_price=sp["entry"],
                  exit_price=exit_price, quantity=sp["qty"], pnl=pnl,
                  score=sp.get("score", 0.0), grade=sp.get("grade", ""))
        except Exception as e:
            logger.warning(f"shadow journal write failed for {sym}: {e}")
        logger.info(f"[SHADOW] {sym} {sp['direction']} closed @ Rs.{exit_price:.2f} "
                    f"P&L Rs.{pnl:+.2f} (signal-quality estimate)")

    def _manage_shadow_positions(self):
        """
        In MANUAL_SIGNALS_ONLY mode the bot places no orders, so it can't see
        real P&L. To still measure the live EDGE for /proof, shadow-track every
        alerted signal: follow price to SL or T1 and journal the hypothetical
        outcome. Conservative model — exit at T1 (win) or SL (loss). This is a
        SIGNAL-QUALITY estimate of the bot's edge, not your actual fills.
        """
        if not self._shadow_positions:
            return
        try:
            ltp_map = get_multiple_ltp(list(self._shadow_positions.keys()), self._dhan)
        except Exception:
            ltp_map = {}
        for sym in list(self._shadow_positions.keys()):
            sp  = self._shadow_positions[sym]
            ltp = ltp_map.get(sym, 0.0)
            if ltp <= 0:
                continue
            exit_price = None
            if sp["direction"] == "LONG":
                if ltp <= sp["sl"]:
                    exit_price = sp["sl"]
                elif ltp >= sp["t1"]:
                    exit_price = sp["t1"]
            else:  # SHORT
                if ltp >= sp["sl"]:
                    exit_price = sp["sl"]
                elif ltp <= sp["t1"]:
                    exit_price = sp["t1"]
            if exit_price is not None:
                self._journal_shadow(sym, sp, exit_price)
                del self._shadow_positions[sym]

    def _square_off_shadows(self):
        """At EOD, journal any open shadow signals at the last traded price."""
        if not self._shadow_positions:
            return
        try:
            ltp_map = get_multiple_ltp(list(self._shadow_positions.keys()), self._dhan)
        except Exception:
            ltp_map = {}
        for sym in list(self._shadow_positions.keys()):
            sp  = self._shadow_positions[sym]
            ltp = ltp_map.get(sym, 0.0) or sp["entry"]
            self._journal_shadow(sym, sp, ltp)
            del self._shadow_positions[sym]

    # -- Position management --------------------------------------------------

    def _manage_positions(self):
        ltp_map = {}
        if self._positions:
            try:
                ltp_map = get_multiple_ltp(list(self._positions.keys()), self._dhan)
            except Exception:
                pass

        to_close: List[str] = []

        for symbol, pos in list(self._positions.items()):
            ltp = ltp_map.get(symbol, 0.0)
            if ltp <= 0:
                continue

            pnl_pct = self._pnl_pct(pos, ltp)

            # Breakeven move
            if not pos.breakeven_moved and pnl_pct >= config.BREAKEVEN_TRIGGER_PCT:
                pos.stop_loss        = pos.entry_price
                pos.breakeven_moved  = True
                self._executor.modify_stop_loss(symbol, pos.entry_price)
                logger.info(f"{symbol}: moved stop to breakeven Rs.{pos.entry_price:.2f}")

            # Stage 2: Partial exit at T1 (50% -> runner)
            if not pos.t1_exited and getattr(config, 'PARTIAL_EXIT_ENABLED', True):
                t1_hit = (pos.direction == "LONG" and ltp >= pos.target_1) or \
                          (pos.direction == "SHORT" and ltp <= pos.target_1)
                if t1_hit:
                    partial_qty = pos.quantity // 2
                    if partial_qty > 0:
                        close_dir = "SHORT" if pos.direction == "LONG" else "LONG"
                        r = self._executor.place_entry_order(symbol, close_dir, partial_qty, ltp, pos.security_id)
                        if r.success:
                            pos.t1_exited = True
                            pos.quantity -= partial_qty
                            partial_pnl = (ltp - pos.entry_price) * partial_qty if pos.direction == "LONG" else (pos.entry_price - ltp) * partial_qty
                            self._stats.total_pnl += partial_pnl
                            pos.stop_loss = pos.entry_price  # trail runner to breakeven
                            _tg_verbose(f"INDIA T1 Partial Exit -- {symbol}\n{partial_qty} shares @ Rs.{ltp:.2f} | Runner to T2=Rs.{pos.target_2:.2f}")

            # Stage 3: Trail runner at 0.3 ATR after T1 exit
            if pos.t1_exited and pos.atr > 0 and getattr(config, 'TRAILING_STOP_ENABLED', True):
                trail = pos.atr * getattr(config, 'TRAILING_TIGHT_ATR', 0.3)
                if pos.direction == "LONG":
                    new_sl = ltp - trail
                    if new_sl > pos.stop_loss:
                        pos.stop_loss = new_sl
                        self._executor.modify_stop_loss(symbol, new_sl)
                else:
                    new_sl = ltp + trail
                    if new_sl < pos.stop_loss:
                        pos.stop_loss = new_sl
                        self._executor.modify_stop_loss(symbol, new_sl)

            # Target 2 hit -- close runner
            if pos.direction == "LONG" and ltp >= pos.target_2:
                logger.info(f"{symbol}: TP2 hit at Rs.{ltp:.2f}")
                to_close.append(symbol)

            elif pos.direction == "SHORT" and ltp <= pos.target_2:
                logger.info(f"{symbol}: TP2 hit at Rs.{ltp:.2f}")
                to_close.append(symbol)

            # Scalp time stop
            elif getattr(pos, 'is_scalp', False) and pos.time_stop_min > 0:
                elapsed = (datetime.now(IST) - pos.entry_time).total_seconds() / 60
                if elapsed >= pos.time_stop_min:
                    logger.info(f"{symbol}: scalp time stop hit ({elapsed:.0f}m >= {pos.time_stop_min}m)")
                    to_close.append(symbol)
                    continue

            # Stop hit (live mode -- Dhan SLM handles it; paper mode: check manually)
            elif not config.LIVE_TRADING_ENABLED:
                if pos.direction == "LONG" and ltp <= pos.stop_loss:
                    logger.info(f"{symbol}: stop hit at Rs.{ltp:.2f}")
                    to_close.append(symbol)
                elif pos.direction == "SHORT" and ltp >= pos.stop_loss:
                    logger.info(f"{symbol}: stop hit at Rs.{ltp:.2f}")
                    to_close.append(symbol)

            # Daily loss circuit (use MAX_DAILY_CAPITAL, never divide by zero)
            _capital = max(config.MAX_DAILY_CAPITAL, 1.0)
            daily_pnl_pct = self._stats.total_pnl / _capital
            if daily_pnl_pct <= -config.DAILY_LOSS_LIMIT_PCT:
                logger.warning("Daily loss limit hit -- closing all positions")
                _tg_verbose("Daily loss limit hit -- all positions being closed")
                self._stats.circuit_hit = True
                to_close = list(self._positions.keys())
                break

        for symbol in set(to_close):
            self._close_position(symbol, ltp_map.get(symbol, 0.0))

    def _close_position(self, symbol: str, exit_price: float):
        pos = self._positions.pop(symbol, None)
        if not pos:
            return

        if exit_price <= 0:
            exit_price = pos.entry_price

        pnl = self._calc_pnl(pos, exit_price)
        self._stats.total_pnl += pnl
        if pnl > 0:
            self._stats.wins += 1
            self._stats.consecutive_losses = 0      # win resets the streak
            if self._stats.loss_guard_active:
                self._stats.loss_guard_active = False
                logger.info("Loss guard lifted after win")
                _tg_verbose("INDIA BOT Loss guard lifted -- win after losing streak, resuming normal trading")
        else:
            self._stats.losses += 1
            self._stats.consecutive_losses += 1
            # After 3 consecutive losses, pause new entries for 30 min
            if self._stats.consecutive_losses >= 3 and not self._stats.loss_guard_active:
                self._stats.loss_guard_active = True
                logger.warning(f"3 consecutive losses -- loss guard activated")
                _tg_verbose(
                    f"INDIA BOT Loss Guard Activated\n"
                    f"3 consecutive losses today.\n"
                    f"Pausing new entries. Bot will resume when:\n"
                    f"  * Next scan finds strong setup (guard auto-lifts on win)\n"
                    f"  * Or market closes (3:20 PM IST)\n"
                    f"Existing positions managed normally."
                )

        logger.info(f"CLOSED {symbol}: P&L Rs.{pnl:+.2f} | streak: {self._stats.consecutive_losses} losses")

        # Record for neural predictor
        try:
            from neural_predictor import record_outcome as _nro
            entry_val = pos.entry_price or 1
            pnl_pct   = pnl / (entry_val * pos.quantity + 1e-9)
            _nro({}, pnl > 0)
        except Exception:
            pass

        # Record for Sortino
        try:
            from institutional_strategies_india import record_trade_result
            record_trade_result(symbol, pnl / (pos.entry_price * pos.quantity + 1e-9))
        except Exception:
            pass

        # Record P&L for EOD report and monthly tracking
        try:
            from daily_intelligence_india import record_trade_close as _rtc
            _rtc(symbol, pnl)
        except Exception:
            pass

        # Persistent trade journal (SQLite) for /monthly and /weekly reports
        try:
            from trade_journal_india import record_trade as _jrec
            _jrec(
                symbol      = symbol,
                direction   = pos.direction,
                entry_price = pos.entry_price,
                exit_price  = exit_price,
                quantity    = pos.quantity,
                pnl         = pnl,
            )
        except Exception as e:
            logger.warning(f"journal write failed for {symbol}: {e}")

        _tg_verbose(
            f"INDIA BOT Position Closed -- {symbol}\n"
            f"--------------------------------\n"
            f"Entry: Rs.{pos.entry_price:.2f} -> Exit: Rs.{exit_price:.2f}\n"
            f"P&L: Rs.{pnl:+,.2f} | Qty: {pos.quantity}\n"
            f"Day P&L: Rs.{self._stats.total_pnl:+,.2f} | "
            f"W:{self._stats.wins} L:{self._stats.losses}"
        )
        self._write_state_file()

    # -- Force square-off -----------------------------------------------------

    def _force_square_off(self):
        if not self._positions:
            return
        logger.info(f"Force square-off: {len(self._positions)} positions")
        _tg_verbose(f"INDIA BOT Force Square-off -- {len(self._positions)} positions closing at 3:20 PM IST | NSE MIS auto-squared")

        open_pos_dhan = self._executor.get_open_positions()
        self._executor.square_off_all(open_pos_dhan)

        # Mark all as closed at last known price
        for symbol in list(self._positions.keys()):
            self._close_position(symbol, 0.0)

    # -- EOD report -----------------------------------------------------------

    def _send_eod_report(self):
        s = self._stats
        win_rate = s.wins / s.trades if s.trades else 0
        filled = max(0, min(10, int(round(win_rate * 10))))
        bar = "X" * filled + "." * (10 - filled)
        _tg_verbose(
            f"INDIA BOT -- EOD Report\n"
            f"--------------------------------\n"
            f"{datetime.now(IST).strftime('%d %b %Y')} | NSE | Dhan\n\n"
            f"Trades: {s.trades}  Wins: {s.wins}  Losses: {s.losses}\n"
            f"Win rate: {win_rate:.0%} [{bar}]\n"
            f"Day P&L: Rs.{s.total_pnl:+,.2f}\n\n"
            f"{'Profitable session!' if s.total_pnl > 0 else ('Loss day -- watchdog reviewing' if s.trades else 'No trades -- filters held')}\n"
            f"--------------------------------"
        )
        # Also trigger the full detailed EOD from daily intelligence (verbose only)
        try:
            if getattr(config, "TELEGRAM_VERBOSE", False):
                from daily_intelligence_india import send_eod_report as _eod
                _eod()
        except Exception:
            pass
        # Monthly auto-report: send on the 1st of the month
        try:
            from trade_journal_india import maybe_send_monthly_summary
            maybe_send_monthly_summary(_tg_verbose)
        except Exception:
            pass

    # -- Helpers --------------------------------------------------------------

    def _kelly_fraction(self) -> float:
        """
        Fractional Kelly position sizing based on rolling session win rate.
        Kelly formula: f = (p*b - q) / b
          p = win probability, q = 1-p, b = reward-to-risk ratio (target/SL)
        We use half-Kelly (50%) for safety -- still captures edge without overbetting.
        Falls back to flat MAX_RISK_PER_TRADE_PCT when sample too small (<5 trades).
        """
        total = self._stats.wins + self._stats.losses
        if total < 5:
            return config.MAX_RISK_PER_TRADE_PCT   # not enough data yet

        p = self._stats.wins / total
        q = 1.0 - p
        # R:R ~= ATR_TP_MULTIPLIER / ATR_SL_MULTIPLIER = 3.0 / 1.5 = 2.0
        b = config.ATR_TP_MULTIPLIER / max(config.ATR_SL_MULTIPLIER, 0.01)
        kelly_full = (p * b - q) / b
        half_kelly = kelly_full * 0.5   # half-Kelly for robustness

        # Clamp: never risk less than 0.1% or more than 1.5% per trade
        return max(0.001, min(0.015, half_kelly))

    def _grade_multiplier(self, grade: str) -> float:
        """
        Size multiplier by signal quality grade from HighAccuracyFilter.
        Grade A+ -> 1.35x (maximum conviction, Grand Slam)
        Grade A  -> 1.00x (solid setup, full size)
        Grade B+ -> 0.80x (good setup, slightly reduced)
        Grade B  -> 0.65x (average setup, smaller bet)
        Grade C  -> 0.45x (marginal, minimum viable position)
        """
        return {"A+": 1.35, "A": 1.00, "B+": 0.80, "B": 0.65, "C": 0.45}.get(grade, 0.65)

    def _calculate_qty(self, sig: IndiaTradeSignal) -> int:
        """
        Kelly Criterion + grade-weighted position sizing.

        Steps:
          1. Half-Kelly fraction based on rolling session win rate
          2. Grade multiplier (A+=1.35x, A=1.0x, B=0.65x, C=0.45x)
          3. Loss guard: halve size if 3 consecutive losses hit today
          4. Cap: max 20% capital in one position (25% for Grand Slams)
        """
        try:
            sl_dist = abs(sig.entry_price - sig.stop_loss)
            if sl_dist <= 0 or sig.entry_price <= 0:
                return 0

            # Kelly-fraction risk amount
            kelly_pct = self._kelly_fraction()
            risk_inr  = config.MAX_DAILY_CAPITAL * kelly_pct

            # Grade-based multiplier
            grade_mult = self._grade_multiplier(getattr(sig, "quality_grade", "B"))

            # Signal size multiplier (from HAF / vol targeting)
            sig_mult = getattr(sig, "size_multiplier", 1.0) or 1.0

            # Base quantity
            qty = int(risk_inr / sl_dist * grade_mult * sig_mult)
            qty = max(1, qty)

            # Loss guard: if 3 consecutive losses, trade at 50% size
            if self._stats.loss_guard_active:
                qty = max(1, qty // 2)

            # Cap: Grand Slam (A+) allowed 25% capital; others 20%
            is_grand_slam = getattr(sig, "quality_grade", "") == "A+"
            cap_pct = 0.25 if is_grand_slam else 0.20
            max_qty = int(config.MAX_DAILY_CAPITAL * cap_pct / (sig.entry_price + 1))

            return min(qty, max_qty)
        except Exception:
            return 0

    def _get_balance(self) -> float:
        if not config.LIVE_TRADING_ENABLED or self._dhan is None:
            return config.MAX_DAILY_CAPITAL
        try:
            resp = self._dhan.get_fund_limits()
            if resp and resp.get("status") == "success":
                return float(resp.get("data", {}).get("availabelBalance", 0))
        except Exception:
            pass
        return config.MAX_DAILY_CAPITAL

    @staticmethod
    def _pnl_pct(pos: OpenPosition, ltp: float) -> float:
        if pos.entry_price <= 0:
            return 0.0
        if pos.direction == "LONG":
            return (ltp - pos.entry_price) / pos.entry_price
        return (pos.entry_price - ltp) / pos.entry_price

    @staticmethod
    def _calc_pnl(pos: OpenPosition, exit_price: float) -> float:
        if pos.direction == "LONG":
            return (exit_price - pos.entry_price) * pos.quantity
        return (pos.entry_price - exit_price) * pos.quantity

    # -- Rich India status builder -------------------------------------------

    def _build_rich_status(self) -> str:
        now = datetime.now(IST)
        s   = self._stats
        cap = config.MAX_DAILY_CAPITAL
        wr  = s.wins / s.trades if s.trades else 0.0
        pnl_pct = s.total_pnl / max(cap, 1) * 100

        filled  = max(0, min(10, int(round(abs(pnl_pct) / (config.DAILY_TARGET_PCT * 100) * 10))))
        bar     = ("█" * filled + "░" * (10 - filled))[:10]

        mode_str   = "LIVE ⚡" if config.LIVE_TRADING_ENABLED else "PAPER 🔒"
        state_str  = "⏸ PAUSED" if s.circuit_hit else "ACTIVE ✅"
        guard_str  = "🛡 LOSS GUARD ON" if s.loss_guard_active else "OFF ✅"

        lines = [
            f"🇮🇳 <b>PSEB — INDIA BOT</b>",
            f"📅 {now.strftime('%d %b %Y')} | {now.strftime('%H:%M')} IST | {mode_str}",
            "─" * 30,
            f"STATUS  {state_str}",
            f"CAPITAL Rs.{cap:,.0f}",
            f"DAY P&L <b>Rs.{s.total_pnl:+,.0f} ({pnl_pct:+.2f}%)</b>",
            f"TARGET  {config.DAILY_TARGET_PCT*100:.1f}% = Rs.{cap*config.DAILY_TARGET_PCT:,.0f} [{bar}]",
            "",
            f"TRADES  {s.trades} today | {s.wins}W / {s.losses}L | WR: {wr:.0%}",
            f"GUARD   {guard_str}",
        ]

        # -- Open positions with live P&L ---
        if self._positions:
            lines.append("")
            lines.append(f"── OPEN POSITIONS ({len(self._positions)}) ──")
            try:
                ltp_map = get_multiple_ltp(list(self._positions.keys()), self._dhan)
            except Exception:
                ltp_map = {}
            for sym, pos in self._positions.items():
                ltp = ltp_map.get(sym, 0.0)
                arrow = "↑" if pos.direction == "LONG" else "↓"
                if ltp > 0:
                    pos_pnl     = self._calc_pnl(pos, ltp)
                    pos_pnl_pct = self._pnl_pct(pos, ltp) * 100
                    be_tag = " [BE]" if pos.breakeven_moved else ""
                    lines.append(
                        f"{arrow} <b>{sym}</b> | Entry: {pos.entry_price:.2f} | "
                        f"CMP: {ltp:.2f} ({pos_pnl_pct:+.1f}%)"
                    )
                    lines.append(
                        f"  P&L: Rs.{pos_pnl:+,.0f}{be_tag} | "
                        f"SL: {pos.stop_loss:.2f} | T1: {pos.target_1:.2f}"
                    )
                else:
                    lines.append(f"{arrow} <b>{sym}</b> | Entry: {pos.entry_price:.2f} | SL: {pos.stop_loss:.2f}")
        else:
            lines.append("")
            lines.append("── No open positions ──")

        # -- OODA Intelligence ---
        lines.append("")
        lines.append("── OODA INTELLIGENCE ──")
        try:
            from regime_classifier_india import get_regime as _get_regime, _cache as _rc
            _get_regime()  # refresh
            regime     = _rc.get("regime", "UNKNOWN")
            regime_conf = _rc.get("confidence", 0.0)
            vix_val    = _rc.get("details", {}).get("vix", 0.0)
            regime_icons = {
                "TRENDING_UP":   "📈", "TRENDING_DOWN": "📉",
                "RANGING":       "↔️", "VOLATILE":      "🌪",
                "UNKNOWN":       "❓",
            }
            icon = regime_icons.get(regime, "❓")
            lines.append(f"Regime: <b>{regime}</b> {icon} (conf: {regime_conf:.0%})")
            if vix_val:
                vix_tag = "🔴 HIGH" if vix_val >= 22 else ("🟡 ELEV" if vix_val >= 17 else "🟢 calm")
                lines.append(f"VIX:    {vix_val:.1f} {vix_tag}")
        except Exception:
            lines.append("Regime: unavailable")

        try:
            from fii_dii_india import get_fii_dii_bias as _fii_bias, _get_cached_fii_dii as _fii_raw
            bias, conf, reason = _fii_bias()
            raw = _fii_raw()
            if raw:
                fii = raw.get("fii_net_cr", 0)
                dii = raw.get("dii_net_cr", 0)
                bias_icon = "🟢" if bias == "BULLISH" else ("🔴" if bias == "BEARISH" else "⚪")
                lines.append(f"FII:    {fii:+.0f}Cr | DII: {dii:+.0f}Cr {bias_icon} {bias}")
            else:
                lines.append(f"FII/DII: {reason[:50]}")
        except Exception:
            pass

        try:
            from nse_option_chain import get_put_call_ratio as _pcr
            pcr = _pcr("NIFTY")
            if pcr:
                pcr_bias = "Bullish" if pcr > 1.1 else ("Bearish" if pcr < 0.8 else "Neutral")
                lines.append(f"PCR:    {pcr:.2f} → {pcr_bias}")
        except Exception:
            pass

        # -- Sector snapshot ---
        try:
            from nse_sector_momentum import _get_sector_returns as _sr
            sret = _sr()
            if sret:
                lines.append("")
                lines.append("── SECTORS ──")
                top3 = sorted(sret.items(), key=lambda x: x[1], reverse=True)[:4]
                parts = []
                for sec, ret in top3:
                    icon = "🔥" if ret > 0.5 else ("✅" if ret > 0 else "⚠️")
                    parts.append(f"{icon} {sec}: {ret:+.1f}%")
                lines.append("  ".join(parts))
        except Exception:
            pass

        # -- Next setups watching (top candidates via base score proxy) ---
        try:
            if self._generator and self._watchlist:
                from data_fetch_dhan import get_ohlcv as _gohlcv
                _top = [s for s in self._watchlist if s not in self._positions][:10]
                _candidates = []
                _rec = self._generator._recognizer
                for _sym in _top:
                    try:
                        _df5 = _gohlcv(_sym, "5m")
                        if _df5 is not None and len(_df5) >= 20:
                            # Same API as generate_signal: compute() then read latest.
                            _df5 = _rec.indicators.compute(_df5)
                            _ind = _rec.indicators.get_latest_indicators(_df5)
                            if _ind is None:
                                continue
                            _dir = self._generator._get_direction(_ind, _df5)
                            if _dir:
                                _sc = self._generator._compute_base_score(_ind, _dir, _df5)
                                if _sc > 50:
                                    _candidates.append((_sym, _dir, _sc))
                    except Exception:
                        pass
                if _candidates:
                    _candidates.sort(key=lambda x: x[2], reverse=True)
                    lines.append("")
                    lines.append("── NEXT WATCHING ──")
                    for _sym, _dir, _sc in _candidates[:3]:
                        _arr = "↑" if _dir == "LONG" else "↓"
                        lines.append(f"• <b>{_sym}</b> {_arr} — Score: {_sc:.0f}")
        except Exception:
            pass

        return "\n".join(lines)

    def _write_state_file(self):
        try:
            s   = self._stats
            cap = config.MAX_DAILY_CAPITAL
            import json as _json
            from pathlib import Path as _Path

            now_ist = datetime.now(IST)
            t = now_ist.time()
            if t < config.PRE_MARKET_START_IST:
                mkt_state = "PRE_MARKET"
            elif t < config.MARKET_OPEN_IST:
                mkt_state = "PRE_OPEN"
            elif t < config.SQUAREOFF_TIME_IST:
                mkt_state = "OPEN"
            else:
                mkt_state = "CLOSED"

            ltp_map = {}
            if self._positions and mkt_state == "OPEN":
                try:
                    ltp_map = get_multiple_ltp(list(self._positions.keys()), self._dhan)
                except Exception:
                    pass

            positions_data = []
            for sym, pos in self._positions.items():
                ltp = ltp_map.get(sym, 0.0)
                pos_pnl = self._calc_pnl(pos, ltp) if ltp > 0 else 0.0
                positions_data.append({
                    "symbol": sym, "direction": pos.direction,
                    "entry_price": pos.entry_price, "ltp": ltp,
                    "stop_loss": pos.stop_loss, "target_1": pos.target_1,
                    "pnl": pos_pnl, "breakeven_moved": pos.breakeven_moved,
                })

            state = {
                "ts": now_ist.isoformat(),
                "market_state": mkt_state,
                "live": config.LIVE_TRADING_ENABLED,
                "capital": cap,
                "daily_pnl": s.total_pnl,
                "daily_pnl_pct": s.total_pnl / max(cap, 1) * 100,
                "trades": s.trades, "wins": s.wins, "losses": s.losses,
                "positions": positions_data,
                "circuit_hit": s.circuit_hit,
                "loss_guard": s.loss_guard_active,
            }
            _Path("/tmp/india_state.json").write_text(_json.dumps(state, indent=2))
        except Exception:
            pass

    # -- Manual order & TradingView command handling --------------------------

    def _handle_manual_order(self, direction: str, parts: list) -> str:
        """
        Parse and stage a manual order from Telegram or TradingView webhook.
        parts: [SYMBOL, QTY] or [SYMBOL, QTY, PRICE]
        Returns reply string.
        """
        if len(parts) < 2:
            return f"Usage: /{'buy' if direction=='LONG' else 'sell'} SYMBOL QTY [PRICE]\nExample: /buy RELIANCE 10 2850"
        symbol = parts[0].upper().replace(".NS", "")
        try:
            qty = int(parts[1])
        except ValueError:
            return "Invalid quantity — must be a whole number"
        if qty <= 0:
            return "Quantity must be > 0"
        limit_price = 0.0
        if len(parts) >= 3:
            try:
                limit_price = float(parts[2])
            except ValueError:
                return "Invalid price"

        # Fetch current LTP
        ltp = 0.0
        try:
            from data_fetch_dhan import get_multiple_ltp as _gltp
            ltp_map = _gltp([symbol], self._dhan)
            ltp = ltp_map.get(symbol, 0.0)
        except Exception:
            pass
        if ltp <= 0:
            try:
                from data_fetch_dhan import get_ohlcv as _gohlcv
                df = _gohlcv(symbol, "5m", "5d")
                if df is not None and not df.empty:
                    ltp = float(df["close"].iloc[-1])
            except Exception:
                pass

        exec_price = limit_price if limit_price > 0 else (ltp if ltp > 0 else 0)
        if exec_price <= 0:
            return f"Could not fetch price for {symbol} — check symbol spelling"

        # Auto SL: 2% for longs, 2% above for shorts
        if direction == "LONG":
            auto_sl = round(exec_price * 0.98, 2)
        else:
            auto_sl = round(exec_price * 1.02, 2)

        order_type = "LIMIT" if limit_price > 0 else "MARKET"
        arrow = "↑ BUY" if direction == "LONG" else "↓ SELL"

        self._pending_manual_order = {
            "symbol": symbol, "direction": direction, "qty": qty,
            "price": exec_price, "limit_price": limit_price,
            "auto_sl": auto_sl, "order_type": order_type,
            "ts": _time.monotonic(), "source": "manual",
        }

        mode_tag = "" if config.LIVE_TRADING_ENABLED else " [PAPER]"
        return (
            f"📋 MANUAL ORDER PENDING{mode_tag}\n"
            f"{'─'*28}\n"
            f"{arrow} {qty} × {symbol}\n"
            f"Type:  {order_type} @ Rs.{exec_price:,.2f}\n"
            f"LTP:   Rs.{ltp:,.2f}\n"
            f"SL:    Rs.{auto_sl:,.2f} (2%)\n"
            f"Value: Rs.{qty * exec_price:,.0f}\n"
            f"{'─'*28}\n"
            f"✅ /confirm — execute\n"
            f"❌ /cancel  — abort\n"
            f"⏱ Expires in 5 minutes"
        )

    def _execute_manual_order(self) -> str:
        """Execute the staged pending manual order."""
        o = getattr(self, "_pending_manual_order", None)
        if not o:
            return "No pending order to confirm."
        if _time.monotonic() - o["ts"] > 300:
            self._pending_manual_order = None
            return "⏱ Order expired (5 min). Re-send the /buy or /sell command."

        self._pending_manual_order = None
        symbol    = o["symbol"]
        direction = o["direction"]
        qty       = o["qty"]
        price     = o["price"]

        from data_fetch_dhan import get_security_id as _gsid
        security_id = _gsid(symbol) or (f"PAPER-{symbol}" if not config.LIVE_TRADING_ENABLED else "")

        if config.LIVE_TRADING_ENABLED and not security_id:
            return f"⚠️ No security_id for {symbol} — cannot place live order. Check symbol."

        result = self._executor.place_entry_order(
            symbol=symbol, direction=direction,
            qty=qty, price=price, security_id=security_id,
        )
        if not result.success:
            return f"❌ Order failed: {result.message}"

        fill_price = result.fill_price or price
        fill_qty   = result.quantity or qty

        # Place auto stop-loss
        sl_result = self._executor.place_stop_order(
            symbol=symbol, direction=direction,
            qty=fill_qty, stop_price=o["auto_sl"], security_id=security_id,
        )

        pos = OpenPosition(
            symbol=symbol, direction=direction,
            entry_price=fill_price, stop_loss=o["auto_sl"],
            target_1=round(fill_price * (1.04 if direction=="LONG" else 0.96), 2),
            target_2=round(fill_price * (1.06 if direction=="LONG" else 0.94), 2),
            quantity=int(fill_qty), security_id=security_id,
            stop_order_id=sl_result.order_id if sl_result.success else "",
        )
        self._positions[symbol] = pos
        self._stats.trades += 1
        self._last_trade_ts = _time.monotonic()
        self._write_state_file()

        arrow = "↑ BUY" if direction == "LONG" else "↓ SELL"
        sl_tag = " ✅" if sl_result.success else " ⚠️ SL not set"
        mode_tag = "" if config.LIVE_TRADING_ENABLED else " [PAPER]"
        return (
            f"✅ MANUAL ORDER FILLED{mode_tag}\n"
            f"{'─'*28}\n"
            f"{arrow} {fill_qty} × {symbol}\n"
            f"Fill:  Rs.{fill_price:,.2f}\n"
            f"SL:    Rs.{o['auto_sl']:,.2f}{sl_tag}\n"
            f"Value: Rs.{fill_qty * fill_price:,.0f}\n"
            f"Use /close {symbol} to exit manually"
        )

    def _handle_tv_webhook_order(self, order: dict) -> None:
        """Receive a TradingView webhook order and send Telegram confirmation request."""
        symbol    = order["symbol"]
        direction = order["direction"]
        qty       = order["qty"]
        price     = order.get("price", 0)
        msg       = order.get("message", "")
        arrow     = "↑ BUY" if direction == "LONG" else "↓ SELL"

        if price <= 0:
            try:
                from data_fetch_dhan import get_ohlcv as _gohlcv
                df = _gohlcv(symbol, "5m", "5d")
                if df is not None and not df.empty:
                    price = float(df["close"].iloc[-1])
            except Exception:
                pass

        auto_sl = round(price * 0.98, 2) if direction == "LONG" else round(price * 1.02, 2)

        self._pending_manual_order = {
            "symbol": symbol, "direction": direction, "qty": qty,
            "price": price, "limit_price": price, "auto_sl": auto_sl,
            "order_type": "MARKET", "ts": _time.monotonic(), "source": "TradingView",
        }

        mode_tag = "" if config.LIVE_TRADING_ENABLED else " [PAPER]"
        _tg(
            f"📡 TRADINGVIEW ALERT{mode_tag}\n"
            f"{'─'*28}\n"
            f"{arrow} {qty} × {symbol} @ Rs.{price:,.2f}\n"
            f"Reason: {msg or 'alert fired'}\n"
            f"SL:     Rs.{auto_sl:,.2f} (2%)\n"
            f"{'─'*28}\n"
            f"✅ /confirm — execute now\n"
            f"❌ /cancel  — skip\n"
            f"⏱ Expires in 5 min"
        )

    # -- Telegram command listener --------------------------------------------

    def _start_telegram_listener(self):
        if not getattr(config, 'TELEGRAM_COMMANDS_ENABLED', True):
            return
        import threading
        self._pending_manual_order = None
        threading.Thread(target=self._telegram_loop, daemon=True).start()
        # Start TradingView webhook server
        try:
            from tradingview_webhook import start_webhook_server, WEBHOOK_PORT
            start_webhook_server()
            _tg_verbose(
                f"📡 TradingView webhook ready\n"
                f"URL: http://YOUR_VPS_IP:{WEBHOOK_PORT}/tv\n"
                f"Commands: /buy SYMBOL QTY [PRICE] | /sell | /chart | /close"
            )
        except Exception as _we:
            logger.warning(f"Webhook server failed to start: {_we}")

    def _telegram_loop(self):
        token = config.TELEGRAM_BOT_TOKEN
        chat  = config.TELEGRAM_CHAT_ID
        if not token or not chat:
            return
        last_id = 0
        try:
            from tradingview_webhook import pending_queue as _tv_queue
        except Exception:
            _tv_queue = None

        while self._running:
            # -- Process TradingView webhook queue ---
            if _tv_queue:
                while not _tv_queue.empty():
                    try:
                        tv_order = _tv_queue.get_nowait()
                        self._handle_tv_webhook_order(tv_order)
                    except Exception:
                        pass

            try:
                import requests
                r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                                 params={"offset": last_id+1, "timeout": 10}, timeout=15)
                for upd in r.json().get("result", []):
                    last_id = upd["update_id"]
                    raw_txt = upd.get("message", {}).get("text", "").strip()
                    # Group support: Telegram appends @botname to commands sent
                    # in groups (e.g. "/today@MyBot"). Strip it so they match.
                    if raw_txt.startswith("/") and "@" in raw_txt.split(" ", 1)[0]:
                        _p = raw_txt.split(" ")
                        _p[0] = _p[0].split("@", 1)[0]
                        raw_txt = " ".join(_p)
                    txt = raw_txt.lower()

                    # -- Existing commands ---
                    if txt == "/kill":
                        _tg("INDIA BOT /kill -- squaring off all India positions")
                        self._handle_shutdown()
                    elif txt == "/pause":
                        self._stats.circuit_hit = True
                        _tg("India bot paused. /resume to continue.")
                    elif txt == "/resume":
                        self._stats.circuit_hit = False
                        _tg("India bot resumed.")
                    elif txt in ("/status", "/india"):
                        try:
                            _tg(self._build_rich_status(), parse_mode="HTML")
                        except Exception as _se:
                            p = self._stats.total_pnl / max(config.MAX_DAILY_CAPITAL, 1)
                            _tg(f"INDIA P&L: Rs.{self._stats.total_pnl:+,.0f} ({p:.2%}) | "
                                f"W:{self._stats.wins} L:{self._stats.losses}")

                    elif txt == "/today":
                        _tg("🔍 Scanning for today's A-grade setups… (a few seconds)")
                        _tg(self._build_today_setups())

                    elif txt == "/monthly":
                        try:
                            now_ist = datetime.now(IST)
                            from trade_journal_india import format_monthly_report
                            _tg(format_monthly_report(now_ist.year, now_ist.month))
                        except Exception as _me:
                            _tg(f"Monthly report error: {_me}")

                    elif txt.startswith("/monthly "):
                        # /monthly 2026-05 or /monthly 5 or /monthly May
                        try:
                            arg = raw_txt.split(None, 1)[1].strip()
                            if "-" in arg:
                                yr, mo = arg.split("-", 1)
                                year_q, month_q = int(yr), int(mo)
                            else:
                                import calendar as _cal
                                month_q = int(arg) if arg.isdigit() else list(_cal.month_abbr).index(arg.capitalize()[:3])
                                year_q  = datetime.now(IST).year
                            from trade_journal_india import format_monthly_report
                            _tg(format_monthly_report(year_q, month_q))
                        except Exception as _me:
                            _tg(f"Usage: /monthly or /monthly 2026-05\nError: {_me}")

                    elif txt == "/weekly":
                        try:
                            from trade_journal_india import format_weekly_report
                            _tg(format_weekly_report())
                        except Exception as _we:
                            _tg(f"Weekly report error: {_we}")

                    elif txt in ("/proof", "/forwardtest", "/ft"):
                        try:
                            from trade_journal_india import format_forward_test_report
                            _tg(format_forward_test_report(), parse_mode="HTML")
                        except Exception as _fe:
                            _tg(f"Forward-test report error: {_fe}")

                    elif txt in ("/data", "/datasource"):
                        try:
                            _tg(self._build_data_status())
                        except Exception as _de:
                            _tg(f"Data status error: {_de}")

                    elif txt == "/invest":
                        _tg("💼 Building your momentum portfolio (the strategy that "
                            "beat buy-and-hold in 15y research)… ~60s")
                        def _do_invest():
                            try:
                                from momentum_invest import format_invest_report
                                _tg(format_invest_report())
                            except Exception as _ie:
                                _tg(f"Invest report error: {_ie}")
                        import threading as _th
                        _th.Thread(target=_do_invest, daemon=True).start()

                    elif txt == "/research":
                        _tg("🔬 Running NSE strategy research on real 15y data… "
                            "(~60-90s, you'll get the verdict here)")
                        def _do_research():
                            try:
                                from nse_research import quick_research
                                _tg(quick_research(capital=config.MAX_DAILY_CAPITAL))
                            except Exception as _re:
                                _tg(f"Research error: {_re}")
                        import threading as _th
                        _th.Thread(target=_do_research, daemon=True).start()

                    # -- Manual order commands ---
                    elif txt.startswith("/buy ") or txt.startswith("/b "):
                        parts = raw_txt.split()[1:]
                        _tg(self._handle_manual_order("LONG", parts))

                    elif txt.startswith("/sell ") or txt.startswith("/s "):
                        parts = raw_txt.split()[1:]
                        _tg(self._handle_manual_order("SHORT", parts))

                    elif txt == "/confirm" or txt == "/yes":
                        _tg(self._execute_manual_order())

                    elif txt == "/cancel" or txt == "/no":
                        if getattr(self, "_pending_manual_order", None):
                            self._pending_manual_order = None
                            _tg("❌ Order cancelled.")
                        else:
                            _tg("No pending order to cancel.")

                    elif txt.startswith("/close "):
                        sym = raw_txt.split()[1].upper().replace(".NS", "")
                        if sym in self._positions:
                            self._close_position(sym, 0.0)
                            _tg(f"✅ Manually closed {sym}")
                        else:
                            _tg(f"{sym} not in open positions. Open: {list(self._positions.keys()) or 'none'}")

                    elif txt.startswith("/chart") or txt.startswith("/tv "):
                        parts = raw_txt.split()
                        sym = parts[1].upper().replace(".NS", "") if len(parts) > 1 else ""
                        if sym:
                            _tg(
                                f"📈 TradingView Chart\n"
                                f"NSE:{sym}\n"
                                f"https://www.tradingview.com/chart/?symbol=NSE:{sym}\n\n"
                                f"Place order: /buy {sym} QTY [PRICE]"
                            )
                        else:
                            _tg("Usage: /chart RELIANCE")

                    elif txt == "/orders" or txt == "/help":
                        _tg(
                            "📋 MANUAL TRADING COMMANDS\n"
                            "────────────────────────\n"
                            "/buy SYMBOL QTY [PRICE]\n"
                            "  e.g. /buy RELIANCE 10\n"
                            "  e.g. /buy RELIANCE 10 2850\n\n"
                            "/sell SYMBOL QTY [PRICE]\n"
                            "  e.g. /sell TCS 5\n\n"
                            "/confirm — execute pending order\n"
                            "/cancel  — abort pending order\n"
                            "/close SYMBOL — exit open position\n"
                            "/chart SYMBOL — TradingView link\n\n"
                            "📊 REPORTS\n"
                            "/status  — live P&L + open positions\n"
                            "/today   — scan for today's A-grade setups\n"
                            "/weekly  — this week's P&L + win rate\n"
                            "/monthly — this month's P&L + win rate\n"
                            "/monthly 2026-05 — specific month report\n"
                            "/proof   — 90-day forward-test GO/NO-GO report\n"
                            "/research — re-run NSE backtest after real costs\n"
                            "/data    — active data feed + live price sample\n"
                            "/invest  — yearly momentum portfolio (~21% backtest)\n\n"
                            "⚙️ CONTROLS\n"
                            "/pause  — pause new entries\n"
                            "/resume — resume after pause\n"
                            "/kill   — emergency stop + square off\n\n"
                            "📡 TradingView webhooks also supported\n"
                            "Set alert webhook to your VPS IP:8888/tv"
                        )

            except Exception as e:
                logger.debug(f"tg_loop: {e}")
            _time.sleep(5)   # faster polling for manual trading responsiveness

    def _handle_shutdown(self, *_):
        logger.info("Shutdown signal received -- squaring off")
        _tg("INDIA BOT Emergency Shutdown -- squaring off all NSE positions now")
        self._force_square_off()
        self._running = False


# -- Entry point --------------------------------------------------------------

def main():
    # Check for duplicate instance
    pid_file = LOG_DIR / "india_bot.pid"
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(
                f"Another India bot instance already running (PID {old_pid}). "
                f"Stop it first with: kill {old_pid}"
            )
            sys.exit(1)
        except ProcessLookupError:
            pass  # old PID is stale
    pid_file.write_text(str(os.getpid()))

    try:
        bot = KingTradesIndia()
        bot.run()
    finally:
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
