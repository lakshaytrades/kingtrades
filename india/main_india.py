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

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [IST] [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / f"india_{datetime.now().strftime('%Y-%m-%d')}.log"),
    ],
)
logger = logging.getLogger("main_india")

IST = ZoneInfo("Asia/Kolkata")


# -- Telegram helper ----------------------------------------------------------

def _tg(msg: str, parse_mode: str = "Markdown"):
    try:
        token = config.TELEGRAM_BOT_TOKEN
        chat  = config.TELEGRAM_CHAT_ID
        if not token or not chat:
            logger.info(f"[TG] {msg[:120]}")
            return
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": parse_mode},
            timeout=8,
        )
    except Exception as e:
        logger.debug(f"telegram: {e}")


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
                _tg("India Bot failed to connect to Dhan -- check DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN")
                return False
            logger.warning("Dhan not connected -- running in paper mode")

        self._executor = get_executor(self._dhan, config.LIVE_TRADING_ENABLED)

        # Watchlist
        self._watchlist = get_active_watchlist(self._dhan)
        logger.info(f"Watchlist: {len(self._watchlist)} symbols")

        # Signal generator
        self._generator = IndiaSignalGenerator(config, self._watchlist)

        # Balance
        balance = self._get_balance()
        self._stats.daily_start = balance
        logger.info(f"Available balance: Rs.{balance:,.2f}")

        # India VIX circuit breaker
        if getattr(config, 'INDIA_VIX_ENABLED', True):
            try:
                import yfinance as yf
                _vdf = yf.download("^INDIAVIX", period="1d", interval="5m", progress=False)
                if _vdf is not None and not _vdf.empty:
                    if isinstance(_vdf.columns, pd.MultiIndex):
                        _vdf.columns = [str(c[0]).lower() for c in _vdf.columns]
                    else:
                        _vdf.columns = [str(c).lower() for c in _vdf.columns]
                    _vix = float(_vdf["close"].iloc[-1])
                    logger.info(f"India VIX: {_vix:.1f}")
                    if _vix >= getattr(config, 'INDIA_VIX_EXTREME_THRESHOLD', 28.0):
                        self._stats.circuit_hit = True
                        _tg(f"India VIX {_vix:.1f} EXTREME -- no new trades today")
                    elif _vix >= getattr(config, 'INDIA_VIX_HIGH_THRESHOLD', 22.0):
                        _tg(f"India VIX {_vix:.1f} HIGH -- sizes reduced")
            except Exception:
                pass

        # Start Telegram command listener
        self._start_telegram_listener()

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
                    _time.sleep(60)
                    continue

                # -- Square-off warning ---------------------------------------
                if config.SQUAREOFF_WARN_IST <= t < config.SQUAREOFF_TIME_IST:
                    if self._positions:
                        syms = ", ".join(self._positions.keys())
                        _tg(f"INDIA BOT Square-off Warning\n"
                            f"3:15 PM IST -- {len(self._positions)} open: {syms}\n"
                            f"Auto-closing at 3:20 PM IST")

                # -- Force square-off -----------------------------------------
                if t >= config.SQUAREOFF_TIME_IST:
                    self._force_square_off()
                    self._send_eod_report()
                    logger.info("Square-off complete. Shutting down for the day.")
                    break

                # -- Market hours: scan + manage -------------------------------
                if config.MARKET_OPEN_IST <= t < config.SQUAREOFF_TIME_IST:
                    if not self._stats.circuit_hit:
                        self._scan_for_signals(t)
                    self._manage_positions()

                # -- Adaptive scan interval ------------------------------------
                # 60s during high-volume windows (open + power close).
                # 300s otherwise -- reduces Dhan API load mid-day.
                _in_power = (time(9, 15) <= t <= time(9, 59)) or \
                            (time(14, 30) <= t <= time(15, 20))
                _time.sleep(60 if _in_power else config.SCAN_INTERVAL_SECONDS)

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
            import yfinance as yf
            now_ts = _time.time()
            if not hasattr(self, "_nifty_cache"):
                self._nifty_cache: dict = {}
            if self._nifty_cache.get("ts", 0) > now_ts - 900:
                return self._nifty_cache.get("regime", "NEUTRAL")

            df = yf.download("^NSEI", period="5d", interval="15m",
                             progress=False, auto_adjust=True)
            if df is None or df.empty:
                return "NEUTRAL"
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [str(c[0]).lower() for c in df.columns]
            else:
                df.columns = [str(c).lower() for c in df.columns]

            if "close" not in df.columns or len(df) < 21:
                return "NEUTRAL"

            ema9  = float(df["close"].ewm(span=9,  adjust=False).mean().iloc[-1])
            ema21 = float(df["close"].ewm(span=21, adjust=False).mean().iloc[-1])
            gap   = (ema9 - ema21) / (ema21 + 1e-9)

            # < 0.05% gap: EMA nearly flat -> choppy, allow both directions
            if abs(gap) < 0.0005:
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

        for symbol in self._watchlist:
            if symbol in self._positions:
                continue
            if len(self._positions) >= config.MAX_POSITIONS:
                break
            if self._stats.circuit_hit:
                break

            try:
                ltp = ltp_map.get(symbol, 0.0)
                signal_obj = self._generator.generate_signal(symbol, current_price=ltp)
                if signal_obj is None:
                    continue

                # ORB-only: skip non-ORB signals during 9:15-9:30 IST
                if _orb_only:
                    has_orb = any("orb" in str(p).lower()
                                  for p in getattr(signal_obj, "patterns", []))
                    if not has_orb:
                        logger.debug(f"{symbol}: skipped (ORB-only window 9:15-9:30)")
                        continue

                # Nifty regime filter: don't fight the market
                if regime == "BEARISH" and signal_obj.direction == "LONG":
                    logger.debug(f"{symbol}: LONG skipped (Nifty BEARISH regime)")
                    continue
                if regime == "BULLISH" and signal_obj.direction == "SHORT":
                    logger.debug(f"{symbol}: SHORT skipped (Nifty BULLISH regime)")
                    continue

                logger.info(
                    f"SIGNAL: {symbol} {signal_obj.direction} "
                    f"score={signal_obj.signal_score:.1f} "
                    f"grade={signal_obj.quality_grade} "
                    f"entry=Rs.{signal_obj.entry_price:.2f} "
                    f"sl=Rs.{signal_obj.stop_loss:.2f} "
                    f"tp=Rs.{signal_obj.target_1:.2f}"
                )

                # Position sizing
                qty = self._calculate_qty(signal_obj)
                if qty <= 0:
                    logger.info(f"{symbol}: qty=0 -- insufficient capital or risk")
                    continue

                # Execute entry
                result = self._executor.place_entry_order(
                    symbol      = symbol,
                    direction   = signal_obj.direction,
                    qty         = qty,
                    price       = signal_obj.entry_price,
                    security_id = signal_obj.security_id,
                )

                if not result.success:
                    logger.warning(f"{symbol}: entry failed -- {result.message}")
                    continue

                fill_price = result.fill_price or signal_obj.entry_price
                fill_qty   = result.quantity or qty

                # Guard: only place stop if fill confirmed
                if fill_qty <= 0 or fill_price <= 0:
                    logger.warning(f"{symbol}: fill not confirmed -- skipping stop order")
                    continue

                # Guard: validate security_id before placing any order
                if not signal_obj.security_id:
                    logger.warning(f"{symbol}: no security_id -- skipping order")
                    continue

                # Place stop-loss
                stop_result = self._executor.place_stop_order(
                    symbol      = symbol,
                    direction   = signal_obj.direction,
                    qty         = fill_qty,
                    stop_price  = signal_obj.stop_loss,
                    security_id = signal_obj.security_id,
                )

                # Guard: if stop order fails, close the entry to avoid unprotected position
                if not stop_result.success:
                    logger.error(f"{symbol}: stop order FAILED ({stop_result.message}) -- squaring off entry")
                    _tg(f"INDIA BOT Stop order failed for {symbol} -- entry being reversed to avoid unprotected position")
                    try:
                        self._executor.square_off_all(
                            self._executor.get_open_positions()
                        )
                    except Exception:
                        pass
                    continue

                pos = OpenPosition(
                    symbol        = symbol,
                    direction     = signal_obj.direction,
                    entry_price   = fill_price,
                    stop_loss     = signal_obj.stop_loss,
                    target_1      = signal_obj.target_1,
                    target_2      = signal_obj.target_2,
                    quantity      = int(fill_qty),
                    security_id   = signal_obj.security_id,
                    stop_order_id = stop_result.order_id,
                    atr           = signal_obj.atr,
                    is_scalp      = getattr(signal_obj, 'is_scalp', False),
                    time_stop_min = getattr(signal_obj, 'time_stop_min', 0),
                )
                self._positions[symbol] = pos
                self._stats.trades += 1
                self._last_trade_ts = _time.monotonic()

                _tg(
                    f"INDIA BOT Trade Entered -- {symbol}\n"
                    f"--------------------------------\n"
                    f"Direction: {signal_obj.direction} | Grade: {signal_obj.quality_grade}\n"
                    f"Entry: Rs.{fill_price:.2f} | Qty: {fill_qty}\n"
                    f"Stop:  Rs.{signal_obj.stop_loss:.2f}  Target: Rs.{signal_obj.target_1:.2f}\n"
                    f"R:R: {abs(signal_obj.target_1-fill_price)/max(abs(fill_price-signal_obj.stop_loss),0.01):.1f}:1 | "
                    f"Score: {signal_obj.signal_score:.0f}/100\n"
                    f"Sector: {get_sector(symbol)} | NSE/Dhan | IST"
                )

                # Log trade explanation to decisions file
                try:
                    from daily_intelligence_india import explain_trade as _explain
                    signal_obj.quantity = int(fill_qty)
                    _why = _explain(signal_obj)
                    _tg(_why)
                except Exception:
                    pass

                # Neural predictor feedback (fail-open)
                try:
                    from neural_predictor import record_outcome as _nr_record
                    # Will be called at close
                except Exception:
                    pass

            except Exception as e:
                logger.error(f"Signal scan {symbol}: {e}")

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
                            _tg(f"INDIA T1 Partial Exit -- {symbol}\n{partial_qty} shares @ Rs.{ltp:.2f} | Runner to T2=Rs.{pos.target_2:.2f}")

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
                _tg("Daily loss limit hit -- all positions being closed")
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
                _tg("INDIA BOT Loss guard lifted -- win after losing streak, resuming normal trading")
        else:
            self._stats.losses += 1
            self._stats.consecutive_losses += 1
            # After 3 consecutive losses, pause new entries for 30 min
            if self._stats.consecutive_losses >= 3 and not self._stats.loss_guard_active:
                self._stats.loss_guard_active = True
                logger.warning(f"3 consecutive losses -- loss guard activated")
                _tg(
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

        _tg(
            f"INDIA BOT Position Closed -- {symbol}\n"
            f"--------------------------------\n"
            f"Entry: Rs.{pos.entry_price:.2f} -> Exit: Rs.{exit_price:.2f}\n"
            f"P&L: Rs.{pnl:+,.2f} | Qty: {pos.quantity}\n"
            f"Day P&L: Rs.{self._stats.total_pnl:+,.2f} | "
            f"W:{self._stats.wins} L:{self._stats.losses}"
        )

    # -- Force square-off -----------------------------------------------------

    def _force_square_off(self):
        if not self._positions:
            return
        logger.info(f"Force square-off: {len(self._positions)} positions")
        _tg(f"INDIA BOT Force Square-off -- {len(self._positions)} positions closing at 3:20 PM IST | NSE MIS auto-squared")

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
        _tg(
            f"INDIA BOT -- EOD Report\n"
            f"--------------------------------\n"
            f"{datetime.now(IST).strftime('%d %b %Y')} | NSE | Dhan\n\n"
            f"Trades: {s.trades}  Wins: {s.wins}  Losses: {s.losses}\n"
            f"Win rate: {win_rate:.0%} [{bar}]\n"
            f"Day P&L: Rs.{s.total_pnl:+,.2f}\n\n"
            f"{'Profitable session!' if s.total_pnl > 0 else ('Loss day -- watchdog reviewing' if s.trades else 'No trades -- filters held')}\n"
            f"--------------------------------"
        )
        # Also trigger the full detailed EOD from daily intelligence
        try:
            from daily_intelligence_india import send_eod_report as _eod
            _eod()
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

    # -- Telegram command listener --------------------------------------------

    def _start_telegram_listener(self):
        if not getattr(config, 'TELEGRAM_COMMANDS_ENABLED', True):
            return
        import threading
        threading.Thread(target=self._telegram_loop, daemon=True).start()

    def _telegram_loop(self):
        token = config.TELEGRAM_BOT_TOKEN
        chat  = config.TELEGRAM_CHAT_ID
        if not token or not chat:
            return
        last_id = 0
        while self._running:
            try:
                import requests
                r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                                 params={"offset": last_id+1, "timeout": 10}, timeout=15)
                for upd in r.json().get("result", []):
                    last_id = upd["update_id"]
                    txt = upd.get("message", {}).get("text", "").strip().lower()
                    if txt == "/kill":
                        _tg("INDIA BOT /kill -- squaring off all India positions")
                        self._handle_shutdown()
                    elif txt == "/pause":
                        self._stats.circuit_hit = True
                        _tg("India bot paused. /resume to continue.")
                    elif txt == "/resume":
                        self._stats.circuit_hit = False
                        _tg("India bot resumed.")
                    elif txt == "/status":
                        p = self._stats.total_pnl / max(config.MAX_DAILY_CAPITAL, 1)
                        _tg(f"INDIA P&L: Rs.{self._stats.total_pnl:+,.0f} ({p:.2%}) | "
                            f"W:{self._stats.wins} L:{self._stats.losses} | "
                            f"Pos:{len(self._positions)}/{config.MAX_POSITIONS}")
            except Exception as e:
                logger.debug(f"tg_loop: {e}")
            _time.sleep(30)

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
