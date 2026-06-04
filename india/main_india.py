"""
main_india.py — KingTrades India Bot
Dhan broker | NSE equity | IST timezone | INR capital

Architecture: parallel to US bot — zero shared state.
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

from zoneinfo import ZoneInfo

# ── Path setup (import parent modules) ────────────────────────────────────────
_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(Path(__file__).parent))

# ── Load env from .env (parent directory) ────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except ImportError:
    pass

# ── Local imports ─────────────────────────────────────────────────────────────
import config_india as config
from auth_dhan              import get_dhan_client, verify_connection
from data_fetch_dhan        import get_ohlcv, get_multiple_ltp
from execution_dhan         import DhanExecutor, get_executor
from watchlist_india        import get_active_watchlist, get_sector
from signal_generator_india import IndiaSignalGenerator, IndiaTradeSignal

# ── Logging ───────────────────────────────────────────────────────────────────
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


# ── Telegram helper ───────────────────────────────────────────────────────────

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


# ── Open position tracking ────────────────────────────────────────────────────

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


# ── Daily stats ───────────────────────────────────────────────────────────────

@dataclass
class DayStats:
    trades:      int   = 0
    wins:        int   = 0
    losses:      int   = 0
    total_pnl:   float = 0.0
    daily_start: float = 0.0
    circuit_hit: bool  = False


# ── Main bot ─────────────────────────────────────────────────────────────────

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

    # ── Startup ────────────────────────────────────────────────────────────────

    def initialize(self) -> bool:
        logger.info("=" * 60)
        logger.info(f"KingTrades India Bot starting — {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
        logger.info(f"Live trading: {config.LIVE_TRADING_ENABLED}")
        logger.info(f"Capital: ₹{config.MAX_DAILY_CAPITAL:,.0f} | Max positions: {config.MAX_POSITIONS}")

        # Dhan connection
        self._dhan = get_dhan_client()
        if not verify_connection(self._dhan):
            if config.LIVE_TRADING_ENABLED:
                logger.error("Dhan connection failed — cannot start in live mode")
                _tg("❌ *India Bot failed to connect to Dhan* — check DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN")
                return False
            logger.warning("Dhan not connected — running in paper mode")

        self._executor = get_executor(self._dhan, config.LIVE_TRADING_ENABLED)

        # Watchlist
        self._watchlist = get_active_watchlist(self._dhan)
        logger.info(f"Watchlist: {len(self._watchlist)} symbols")

        # Signal generator
        self._generator = IndiaSignalGenerator(config, self._watchlist)

        # Balance
        balance = self._get_balance()
        self._stats.daily_start = balance
        logger.info(f"Available balance: ₹{balance:,.2f}")

        mode = "🔴 LIVE" if config.LIVE_TRADING_ENABLED else "📄 PAPER"
        _tg(
            f"🇮🇳 *KingTrades India Bot Started*\n"
            f"Mode: {mode}\n"
            f"Capital: ₹{config.MAX_DAILY_CAPITAL:,.0f}\n"
            f"Watchlist: {len(self._watchlist)} NSE symbols\n"
            f"Gates: 26 | Frameworks: 8 | ML-scored\n"
            f"Market opens: 9:15 AM IST"
        )
        return True

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self):
        if not self.initialize():
            return

        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT,  self._handle_shutdown)

        while self._running:
            try:
                now = datetime.now(IST)
                t   = now.time()

                # ── Pre-market: wait ──────────────────────────────────────────
                if t < config.PRE_MARKET_START_IST:
                    _time.sleep(60)
                    continue

                # ── Square-off warning ────────────────────────────────────────
                if config.SQUAREOFF_WARN_IST <= t < config.SQUAREOFF_TIME_IST:
                    if self._positions:
                        _tg(f"⚠️ *3:15 PM IST* — {len(self._positions)} open positions. "
                            f"Squaring off at 3:20 PM IST.")

                # ── Force square-off ──────────────────────────────────────────
                if t >= config.SQUAREOFF_TIME_IST:
                    self._force_square_off()
                    self._send_eod_report()
                    logger.info("Square-off complete. Shutting down for the day.")
                    break

                # ── Market hours: scan + manage ───────────────────────────────
                if config.MARKET_OPEN_IST <= t < config.SQUAREOFF_TIME_IST:
                    if not self._stats.circuit_hit:
                        self._scan_for_signals()
                    self._manage_positions()

                _time.sleep(config.SCAN_INTERVAL_SECONDS)

            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                _time.sleep(30)

    # ── Signal scan ────────────────────────────────────────────────────────────

    def _scan_for_signals(self):
        if len(self._positions) >= config.MAX_POSITIONS:
            return

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

                logger.info(
                    f"SIGNAL: {symbol} {signal_obj.direction} "
                    f"score={signal_obj.signal_score:.1f} "
                    f"grade={signal_obj.quality_grade} "
                    f"entry=₹{signal_obj.entry_price:.2f} "
                    f"sl=₹{signal_obj.stop_loss:.2f} "
                    f"tp=₹{signal_obj.target_1:.2f}"
                )

                # Position sizing
                qty = self._calculate_qty(signal_obj)
                if qty <= 0:
                    logger.info(f"{symbol}: qty=0 — insufficient capital or risk")
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
                    logger.warning(f"{symbol}: entry failed — {result.message}")
                    continue

                fill_price = result.fill_price or signal_obj.entry_price
                fill_qty   = result.quantity or qty

                # Guard: only place stop if fill confirmed
                if fill_qty <= 0 or fill_price <= 0:
                    logger.warning(f"{symbol}: fill not confirmed — skipping stop order")
                    continue

                # Place stop-loss
                stop_result = self._executor.place_stop_order(
                    symbol      = symbol,
                    direction   = signal_obj.direction,
                    qty         = fill_qty,
                    stop_price  = signal_obj.stop_loss,
                    security_id = signal_obj.security_id,
                )

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
                )
                self._positions[symbol] = pos
                self._stats.trades += 1

                _tg(
                    f"✅ *Trade Entered* — {symbol}\n"
                    f"Direction: {signal_obj.direction}\n"
                    f"Entry: ₹{fill_price:.2f} | Qty: {fill_qty}\n"
                    f"Stop: ₹{signal_obj.stop_loss:.2f} | Target: ₹{signal_obj.target_1:.2f}\n"
                    f"Score: {signal_obj.signal_score:.0f}/100 | Grade: {signal_obj.quality_grade}\n"
                    f"Sector: {get_sector(symbol)}"
                )

                # Neural predictor feedback (fail-open)
                try:
                    from neural_predictor import record_outcome as _nr_record
                    # Will be called at close
                except Exception:
                    pass

            except Exception as e:
                logger.error(f"Signal scan {symbol}: {e}")

    # ── Position management ────────────────────────────────────────────────────

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
                logger.info(f"{symbol}: moved stop to breakeven ₹{pos.entry_price:.2f}")

            # Target 1 hit
            if pos.direction == "LONG" and ltp >= pos.target_1:
                logger.info(f"{symbol}: TP1 hit at ₹{ltp:.2f}")
                to_close.append(symbol)

            elif pos.direction == "SHORT" and ltp <= pos.target_1:
                logger.info(f"{symbol}: TP1 hit at ₹{ltp:.2f}")
                to_close.append(symbol)

            # Stop hit (live mode — Dhan SLM handles it; paper mode: check manually)
            elif not config.LIVE_TRADING_ENABLED:
                if pos.direction == "LONG" and ltp <= pos.stop_loss:
                    logger.info(f"{symbol}: stop hit at ₹{ltp:.2f}")
                    to_close.append(symbol)
                elif pos.direction == "SHORT" and ltp >= pos.stop_loss:
                    logger.info(f"{symbol}: stop hit at ₹{ltp:.2f}")
                    to_close.append(symbol)

            # Daily loss circuit
            daily_pnl_pct = self._stats.total_pnl / (config.MAX_DAILY_CAPITAL + 1)
            if daily_pnl_pct <= -config.DAILY_LOSS_LIMIT_PCT:
                logger.warning("Daily loss limit hit — closing all positions")
                _tg("🛑 *Daily loss limit hit* — all positions being closed")
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
        else:
            self._stats.losses += 1

        logger.info(f"CLOSED {symbol}: P&L ₹{pnl:+.2f}")

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

        _tg(
            f"{'🟢' if pnl > 0 else '🔴'} *Position Closed* — {symbol}\n"
            f"Entry: ₹{pos.entry_price:.2f} | Exit: ₹{exit_price:.2f}\n"
            f"P&L: ₹{pnl:+.2f} | Qty: {pos.quantity}\n"
            f"Day total: ₹{self._stats.total_pnl:+.2f}"
        )

    # ── Force square-off ────────────────────────────────────────────────────────

    def _force_square_off(self):
        if not self._positions:
            return
        logger.info(f"Force square-off: {len(self._positions)} positions")
        _tg(f"🔔 *Square-off* — closing {len(self._positions)} positions at 3:20 PM IST")

        open_pos_dhan = self._executor.get_open_positions()
        self._executor.square_off_all(open_pos_dhan)

        # Mark all as closed at last known price
        for symbol in list(self._positions.keys()):
            self._close_position(symbol, 0.0)

    # ── EOD report ────────────────────────────────────────────────────────────

    def _send_eod_report(self):
        s = self._stats
        win_rate = s.wins / s.trades if s.trades else 0
        _tg(
            f"📊 *KingTrades India — EOD Report*\n"
            f"Date: {datetime.now(IST).strftime('%Y-%m-%d')}\n\n"
            f"Trades: {s.trades} | Wins: {s.wins} | Losses: {s.losses}\n"
            f"Win rate: {win_rate:.0%}\n"
            f"Day P&L: ₹{s.total_pnl:+.2f}\n"
            f"{'✅ Profitable day!' if s.total_pnl > 0 else '🔴 Loss day — review logs'}"
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _calculate_qty(self, sig: IndiaTradeSignal) -> int:
        """Position size: risk = MAX_RISK_PER_TRADE_PCT of capital / SL distance."""
        try:
            sl_dist = abs(sig.entry_price - sig.stop_loss)
            if sl_dist <= 0:
                return 0
            risk_inr   = config.MAX_DAILY_CAPITAL * config.MAX_RISK_PER_TRADE_PCT
            qty        = int(risk_inr / sl_dist)
            qty        = max(1, int(qty * sig.size_multiplier))
            # Cap: single position max 20% of capital
            max_qty = int(config.MAX_DAILY_CAPITAL * 0.20 / (sig.entry_price + 1))
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

    def _handle_shutdown(self, *_):
        logger.info("Shutdown signal received — squaring off")
        _tg("⚠️ *India Bot shutdown* — closing all positions")
        self._force_square_off()
        self._running = False


# ── Entry point ────────────────────────────────────────────────────────────────

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
