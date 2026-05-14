"""
execution_groww.py — NSE Momentum Groww AI Bot
Live Order Placement, Modification, Cancellation via growwapi

⚠️ WARNING: THIS MODULE PLACES REAL ORDERS WITH REAL MONEY ON GROWW.
⚠️ ALL orders use MIS (Margin Intraday Square-off) product type.
⚠️ LIVE_TRADING_ENABLED must be True in .env for real order placement.
⚠️ Start with very small capital. Monitor manually at first.

Server runs in UK (UTC) — all timestamps in IST.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from zoneinfo import ZoneInfo

from utils import format_ist_timestamp, get_current_ist_time, round_to_tick_size
from auth_groww import get_groww_token
from risk_manager import Position, RiskManager
from signal_generator import TradeSignal

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class GrowwIPBlockedError(Exception):
    """Raised when Groww rejects order placement due to unregistered server IP."""

_ip_block_last_alerted: Optional[datetime] = None  # throttle spam alerts

# SEBI-compliant trade journal DB
import os as _os
_os.makedirs("logs/trades", exist_ok=True)
TRADE_DB_PATH = "logs/trades/trade_journal.db"


def _get_outbound_ip() -> str:
    """Return this server's public IP — used in IP-whitelist error messages."""
    try:
        import requests as _r
        return _r.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        return "your-vps-ip"


class OrderResult:
    def __init__(self, success: bool, order_id: str = "", message: str = "",
                 raw: dict = None):
        self.success = success
        self.order_id = order_id
        self.message = message
        self.raw = raw or {}
        self.timestamp = format_ist_timestamp()

    def __repr__(self):
        status = "✅" if self.success else "❌"
        return f"{status} OrderResult(id={self.order_id}, msg={self.message})"


def _notify(subject: str, body: str) -> None:
    """
    Fire-and-forget alert — Telegram primary, email optional fallback.
    Never blocks order execution.
    """
    import threading

    def _send():
        # Telegram (primary)
        try:
            import requests as _r
            import os as _os
            bot_token = _os.getenv("TELEGRAM_BOT_TOKEN", "")
            chat_id = _os.getenv("TELEGRAM_CHAT_ID", "")
            if bot_token and chat_id:
                _r.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "parse_mode": "HTML",
                          "text": f"<b>{subject}</b>\n\n{body}"},
                    timeout=10,
                )
        except Exception:
            pass
        # Email (optional fallback — only if configured)
        try:
            import os as _os
            if _os.getenv("GMAIL_APP_PASSWORD") or _os.getenv("BREVO_API_KEY"):
                from email_reporter import send_alert
                send_alert(subject, body)
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()


class GrowwExecutor:
    """
    Live order execution engine for Groww.
    All orders are MIS (intraday). No delivery orders.
    """

    def __init__(self, risk_manager: RiskManager, live_enabled: bool = False):
        self.risk_manager = risk_manager
        self.live_enabled = live_enabled
        self._api = None
        self._init_api()
        self._init_db()

        if self.live_enabled:
            logger.warning(
                f"[{format_ist_timestamp()}] ⚡ LIVE TRADING ENABLED — "
                "REAL ORDERS WILL BE PLACED ON GROWW"
            )
        else:
            logger.info(
                f"[{format_ist_timestamp()}] 🔒 DRY RUN MODE — "
                "No real orders will be placed"
            )

    def _init_api(self):
        """Initialize Groww API client."""
        try:
            from growwapi import GrowwAPI
            token = get_groww_token()
            if token:
                self._api = GrowwAPI(token)
                # Log available order-placement methods for diagnostics
                order_methods = [m for m in dir(self._api)
                                 if not m.startswith("_") and
                                 any(k in m.lower() for k in ("order", "place", "trade", "buy", "sell"))]
                logger.info(
                    f"[{format_ist_timestamp()}] GrowwAPI executor initialized | "
                    f"Order methods: {order_methods}"
                )
        except ImportError:
            logger.error("growwapi not installed — order execution disabled")
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Executor API init error: {e}")

    # --------------------------------------------------------
    # ROBUST ORDER PLACEMENT (method-name discovery)
    # --------------------------------------------------------

    _ORDER_METHOD_NAMES = [
        "place_order",
        "place_equity_order",
        "place_mis_order",
        "create_order",
        "new_order",
        "order",
        "place_new_order",
    ]

    def _call_place_order(self, order_params: dict) -> Optional[dict]:
        """
        Call Groww place_order with the given params.
        Falls back through a priority list of method name variants in case the
        installed SDK version differs. On any successful call (even None response)
        returns the response; only logs an error if NO method name matches at all.
        """
        if not self._api:
            return None

        # Build alternate params in case SDK uses different key names
        alt_params = dict(order_params)
        # Some SDK versions use "symbol" instead of "trading_symbol"
        if "trading_symbol" in alt_params:
            alt_params["symbol"] = alt_params.pop("trading_symbol")
        # Some SDK versions use "product_type" instead of "product"
        if "product" in alt_params:
            alt_params["product_type"] = alt_params.pop("product")
        # Some SDK versions omit "segment"
        alt_params_noseg = {k: v for k, v in order_params.items() if k != "segment"}

        tried = []
        for method_name in self._ORDER_METHOD_NAMES:
            if not hasattr(self._api, method_name):
                continue
            fn = getattr(self._api, method_name)
            tried.append(method_name)

            for label, params in (
                ("primary", order_params),
                ("alt",     alt_params),
                ("noseg",   alt_params_noseg),
            ):
                try:
                    resp = fn(**params)
                    logger.info(
                        f"[{format_ist_timestamp()}] place_order called via "
                        f"'{method_name}' ({label} params) → {resp}"
                    )
                    # Return whatever the SDK gives (None = order rejected at API level)
                    return resp
                except TypeError as te:
                    logger.debug(
                        f"[{format_ist_timestamp()}] '{method_name}' ({label}) "
                        f"TypeError: {te} — trying next param set"
                    )
                except Exception as e:
                    err_str = str(e).lower()
                    # IP whitelist errors affect ALL methods — bail immediately
                    if any(kw in err_str for kw in (
                        "ip", "inactive", "registered ip", "not whitelisted",
                        "ip not", "ip address", "allowed ip",
                    )):
                        raise GrowwIPBlockedError(str(e))
                    logger.warning(
                        f"[{format_ist_timestamp()}] '{method_name}' ({label}) error: {e}"
                    )
                    break  # Non-TypeError: method exists but call rejected → next method

        if not tried:
            # Log all API methods so we know what to add next time
            all_methods = [m for m in dir(self._api) if not m.startswith("_")]
            logger.error(
                f"[{format_ist_timestamp()}] ❌ No order method found in SDK. "
                f"Available API methods: {all_methods}"
            )
        return None

    def _init_db(self):
        """Initialize SEBI-compliant trade journal SQLite DB."""
        Path(TRADE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(TRADE_DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                symbol TEXT,
                direction TEXT,
                quantity INTEGER,
                entry_price REAL,
                exit_price REAL,
                stop_loss REAL,
                target_1 REAL,
                target_2 REAL,
                pnl REAL,
                pnl_pct REAL,
                product TEXT DEFAULT 'MIS',
                signal_score REAL,
                patterns TEXT,
                entry_time TEXT,
                exit_time TEXT,
                exit_reason TEXT,
                atr REAL,
                live_trade INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    # --------------------------------------------------------
    # ENTRY ORDERS
    # --------------------------------------------------------

    def place_entry_order(self, signal: TradeSignal) -> OrderResult:
        """
        Place entry order based on a TradeSignal.
        Uses LIMIT order near current price, falls back to MARKET.

        Args:
            signal: TradeSignal from signal_generator

        Returns:
            OrderResult with order_id on success
        """
        # ── F&O ELIGIBILITY CHECK (SELL/SHORT orders only) ──────────────
        # NSE only allows intraday short-selling on F&O segment stocks.
        # Attempting to short an equity-only stock → Groww REJECTS the order.
        # Rejection after position tracking = phantom position risk.
        if signal.direction in ("SHORT", "SELL"):
            try:
                from nse_fo_list import is_fo_eligible
                if not is_fo_eligible(signal.symbol):
                    logger.warning(
                        f"[{format_ist_timestamp()}] ⛔ SHORT BLOCKED: {signal.symbol} "
                        "is NOT F&O eligible — intraday short-selling not allowed "
                        "on equity-only stocks. Order would be rejected by Groww."
                    )
                    return OrderResult(
                        False,
                        message=(
                            f"{signal.symbol} not F&O eligible — "
                            "cannot short equity-only stock on NSE"
                        ),
                    )
            except Exception as e:
                logger.debug(f"F&O check error (allowing trade): {e}")

        # ── Fetch live balance BEFORE risk check so capital is current ──────
        from data_fetch_groww import get_data_fetcher
        fetcher = get_data_fetcher()
        balance = fetcher.get_account_balance()
        available = balance.get("available", 0)

        # Mid-session auth recovery: if Groww returns 0 twice in a row,
        # the token may have expired — attempt a silent TOTP re-login
        if available <= 0:
            try:
                from auth_groww import get_auth_manager
                _auth = get_auth_manager()
                if _auth and hasattr(_auth, "refresh_token_if_needed"):
                    _refreshed = _auth.refresh_token_if_needed()
                    if _refreshed:
                        balance = fetcher.get_account_balance()
                        available = balance.get("available", 0)
                        logger.info(
                            f"[{format_ist_timestamp()}] Mid-session token refresh: "
                            f"new balance=₹{available:,.0f}"
                        )
            except Exception as _e:
                logger.debug(f"Mid-session token refresh skipped: {_e}")

        # update_balance() guards against 0 — keeps last known good capital
        self.risk_manager.update_balance(available)

        logger.info(
            f"[{format_ist_timestamp()}] PRE-TRADE: {signal.symbol} | "
            f"API balance: ₹{available:,.0f} | "
            f"Risk capital: ₹{self.risk_manager.state.daily_capital:,.0f} | "
            f"Score: {signal.signal_score:.0f} | "
            f"Live: {self.live_enabled}"
        )

        # Pre-trade risk check
        can_trade = self.risk_manager.can_take_trade(signal.symbol, signal.direction)
        if not can_trade["allowed"]:
            logger.warning(
                f"[{format_ist_timestamp()}] Trade BLOCKED: {signal.symbol} — "
                f"{can_trade['reason']}"
            )
            return OrderResult(False, message=can_trade["reason"])

        # ── Sentiment AI — block on bad news ─────────────────────────────
        try:
            from sentiment_ai import get_sentiment_ai
            from news_filter import get_news_filter
            _headlines = []
            try:
                _headlines = get_news_filter().get_symbol_sentiment(signal.symbol).get("headlines", [])
            except Exception:
                pass
            if _headlines:
                _sent = get_sentiment_ai()
                block, reason = _sent.should_block_trade(signal.symbol, signal.direction, _headlines)
                if block:
                    logger.warning(f"[{format_ist_timestamp()}] SENTIMENT BLOCK: {signal.symbol} — {reason}")
                    return OrderResult(False, message=f"Sentiment: {reason}")
                signal._sentiment_headlines = _headlines
        except Exception as e:
            logger.debug(f"Sentiment check skipped: {e}")

        # ── Shared indicator refs (reused by supervisor, win_predictor, RL) ─────
        _ind = signal.indicators
        _vwap_dev = 0.0
        if _ind and _ind.vwap and signal.entry_price:
            _vwap_dev = (signal.entry_price - _ind.vwap) / _ind.vwap * 100
        _mtf = signal.timeframe_alignment or {}
        _aligned = _mtf.get("aligned_count", 0)
        if _aligned == 3:
            _mtf_str = "STRONG_BULLISH" if signal.direction == "LONG" else "STRONG_BEARISH"
        elif _aligned >= 2:
            _mtf_str = "BULLISH" if signal.direction == "LONG" else "BEARISH"
        else:
            _mtf_str = _mtf.get("alignment", "NEUTRAL")
        _h = get_current_ist_time().hour
        _session = (
            "OPENING_DRIVE" if _h < 10
            else "MIDDAY" if 11 <= _h < 13
            else "POWER_HOUR" if 14 <= _h < 15
            else "NORMAL"
        )
        # Pairs mean-reversion and momentum burst signals are designed for
        # midday (11-13:30 IST) — let them bypass the supervisor midday block
        _is_special_strategy = any(
            p.startswith("PAIRS") or p.startswith("BURST")
            for p in (signal.patterns or [])
        )
        if _is_special_strategy and _session == "MIDDAY":
            _session = "AFTERNOON"   # Supervisor allows AFTERNOON; midday block skipped

        # ── Trade Supervisor — 12-rule expert system ──────────────────────────
        try:
            from trade_supervisor import review_signal as _ts_review
            _ts_data = {
                "symbol":             signal.symbol,
                "direction":          signal.direction,
                "score":              signal.signal_score,
                "entry_price":        signal.entry_price,
                "stop_loss":          signal.stop_loss,
                "target":             signal.target_1,
                "rsi":                _ind.rsi if _ind else 50.0,
                "macd_hist":          _ind.macd_hist if _ind else 0.0,
                "vwap_deviation_pct": _vwap_dev,
                "volume_ratio":       _ind.volume_ratio if _ind else 1.0,
                "mtf_alignment":      _mtf_str,
                "session":            _session,
                "nifty_trend":        "neutral",
            }
            _review = _ts_review(_ts_data)
            signal._claude_review = _review
            if not _review.get("approved", True):
                logger.info(
                    f"[{format_ist_timestamp()}] SUPERVISOR REJECT: {signal.symbol} "
                    f"— {_review.get('reason')}"
                )
                return OrderResult(False, message=f"Supervisor: {_review.get('reason')}")
            logger.debug(
                f"[{format_ist_timestamp()}] Supervisor APPROVED: {signal.symbol} "
                f"conf={_review.get('confidence')}%"
            )
        except Exception as e:
            signal._claude_review = {"approved": True, "confidence": 70, "skipped": True}
            logger.debug(f"Supervisor check skipped: {e}")

        # ── Win Predictor — RF-based win probability / size adjustment ─────────
        try:
            from win_predictor import get_win_predictor, conviction_adjustment, should_block_by_win_prob
            _ist_now = get_current_ist_time()
            _wp_data = {
                "signal_score":   signal.signal_score,
                "rsi":            _ind.rsi if _ind else 50.0,
                "volume_ratio":   _ind.volume_ratio if _ind else 1.0,
                "hour":           _ist_now.hour,
                "day_of_week":    _ist_now.weekday(),
                "direction":      signal.direction,
                "session":        _session,
                "macd_hist":      _ind.macd_hist if _ind else 0.0,
                "vwap_deviation": _vwap_dev,
            }
            _win_prob, _confident = get_win_predictor().predict(_wp_data)
            if _confident and should_block_by_win_prob(_win_prob):
                logger.info(
                    f"[{format_ist_timestamp()}] WIN_PRED BLOCK: {signal.symbol} "
                    f"— win_prob={_win_prob:.0%} too low"
                )
                return OrderResult(False, message=f"WinPredictor: win_prob={_win_prob:.0%} too low")
            _adj = conviction_adjustment(_win_prob)
            if _adj > 0 and _confident:
                old_mult = getattr(signal, "size_multiplier", 1.0)
                signal.size_multiplier = round(old_mult * (1 + _adj / 100), 3)
                logger.debug(
                    f"[{format_ist_timestamp()}] WinPredictor boost: {signal.symbol} "
                    f"win_prob={_win_prob:.0%} mult {old_mult:.2f}→{signal.size_multiplier:.2f}"
                )
        except Exception as e:
            logger.debug(f"WinPredictor check skipped: {e}")

        # ── Elite Brain — 12-module fusion (Grand Slam detector) ─────────────
        # Pairs/burst signals are pre-validated by their own engines; skip elite
        # brain's module-count gate (it requires 3+ aligned modules, which pairs
        # signals don't have — they lack SM/PM/MTF data by design)
        if not _is_special_strategy:
            try:
                from elite_brain import make_elite_decision
                _eb_ctx = {
                    "oc_score":            getattr(signal, "_oc_score", 0),
                    "fii_mult":            getattr(signal, "_fii_mult", 1.0),
                    "vp_score":            getattr(signal, "_vp_score", 0),
                    "vp_notes":            [],
                    "regime_name":         getattr(signal, "_regime_name", "UNKNOWN"),
                    "regime_strategy":     getattr(signal, "_regime_strategy", "MOMENTUM"),
                    "regime_size_mult":    getattr(signal, "_regime_size_mult", 1.0),
                    "regime_preferred_dir":getattr(signal, "_regime_dir", "BOTH"),
                    "regime_tradeable":    getattr(signal, "_regime_tradeable", True),
                    "overnight_bias":      0,
                    "sentiment_score":     0,
                    "nse_score":           0,
                    "mtf_alignment":       signal.timeframe_alignment or {},
                }
                _eb_review = signal._claude_review if hasattr(signal, "_claude_review") else None
                _eb_decision = make_elite_decision(
                    symbol             = signal.symbol,
                    direction          = signal.direction,
                    signal_score       = signal.signal_score,
                    ctx                = _eb_ctx,
                    supervisor_review  = _eb_review,
                    win_prob           = _win_prob if "_win_prob" in dir() else 0.55,
                    win_prob_confident = _confident if "_confident" in dir() else False,
                    sm_score           = getattr(signal, "_sm_score", None),
                    pm_score           = getattr(signal, "_pm_score", None),
                )
                if not _eb_decision.approved:
                    logger.info(
                        f"[{format_ist_timestamp()}] ELITE BRAIN REJECT: {signal.symbol} "
                        f"— {_eb_decision.reject_reason} "
                        f"({_eb_decision.aligned_count}/{_eb_decision.total_modules} modules)"
                    )
                    return OrderResult(False, message=_eb_decision.reject_reason)

                # Apply elite brain size multiplier if larger than current
                if _eb_decision.size_multiplier > signal.size_multiplier:
                    signal.size_multiplier = round(_eb_decision.size_multiplier, 2)

                if _eb_decision.grand_slam:
                    logger.info(
                        f"[{format_ist_timestamp()}] 🏆 GRAND SLAM: {signal.symbol} "
                        f"{signal.direction} — {_eb_decision.aligned_count}/12 modules "
                        f"| conviction={_eb_decision.conviction_score:.0f} "
                        f"| size={signal.size_multiplier:.1f}x"
                    )
                    signal.rationale = f"[GRAND SLAM] {signal.rationale}"
            except Exception as e:
                logger.debug(f"Elite Brain check skipped: {e}")

        # ── RL Brain — institution-level portfolio + learned conviction check ──
        try:
            from rl_agent import LakshKingRL
            _mtf = signal.timeframe_alignment or {}
            _aligned = _mtf.get("aligned_count", 0)
            if _aligned == 3:
                _mtf_str = "STRONG_BULLISH" if signal.direction == "LONG" else "STRONG_BEARISH"
            elif _aligned >= 2:
                _mtf_str = "BULLISH" if signal.direction == "LONG" else "BEARISH"
            else:
                _mtf_str = _mtf.get("alignment", "NEUTRAL")
            # Reuse the corrected _session (already bypass-adjusted for pairs/burst)
            market_data = {
                "rsi":                _ind.rsi if _ind else 50.0,
                "macd_hist":          _ind.macd_hist if _ind else 0.0,
                "vwap_deviation_pct": _vwap_dev,
                "volume_ratio":       _ind.volume_ratio if _ind else 1.0,
                "mtf_alignment":      _mtf_str,
                "patterns":           signal.patterns,
                "session":            _session,
                "nifty_trend":        "neutral",
                "signal_score":       signal.signal_score,
            }
            risk_amount = self.risk_manager.state.daily_capital * 0.005  # 0.5%
            rl_decision = LakshKingRL.signal(
                signal.symbol, signal.direction, market_data, risk_amount
            )
            if rl_decision == "SKIP":
                logger.warning(
                    f"[{format_ist_timestamp()}] RL SKIP: {signal.symbol} "
                    "— portfolio or learned Q-table blocked this trade"
                )
                return OrderResult(False, message="RL brain: trade skipped (portfolio filter or low conviction)")
        except Exception as e:
            logger.debug(f"RL check skipped: {e}")

        # Recalculate quantity with live balance, apply signal grade size multiplier
        sizing = self.risk_manager.calculate_position_size(
            symbol       = signal.symbol,
            entry_price  = signal.entry_price,
            stop_loss    = signal.stop_loss,
            direction    = signal.direction,
            size_multiplier = getattr(signal, "size_multiplier", 1.0),
            signal_rr    = getattr(signal, "risk_reward", 2.0),
        )
        quantity = sizing.get("quantity", 0)
        logger.info(
            f"[{format_ist_timestamp()}] SIZING: {signal.symbol} qty={quantity} | "
            f"{sizing.get('reason', 'OK')} | "
            f"capital=₹{sizing.get('capital_used', 0):,.0f} | "
            f"session={sizing.get('session', '?')}"
        )
        if quantity <= 0:
            return OrderResult(
                False,
                message=(
                    f"Position size = 0 — {sizing.get('reason', 'insufficient capital')} "
                    f"(capital=₹{self.risk_manager.state.daily_capital:,.0f})"
                )
            )
        # Apply grade-based size multiplier from HighAccuracyFilter
        size_mult = getattr(signal, "size_multiplier", 1.0)
        if size_mult != 1.0:
            quantity = max(1, int(quantity * size_mult))
            logger.info(
                f"[{format_ist_timestamp()}] Size adjusted by {size_mult:.1f}x "
                f"(Grade {getattr(signal, 'quality_grade', 'B')}) → {quantity} qty"
            )

        # ── Dynamic Leverage — scale up on high-conviction setups ─────────
        # Combines: signal score + Claude confidence + RL Q-edge + MTF + block deal + sector
        # Multiplier range: 0.5x (marginal) → 3.0x (institutional-grade)
        try:
            from dynamic_leverage import calculate_conviction, apply_conviction_cap
            _review = getattr(signal, "_claude_review", {})
            _rl_agent = None
            _rl_state = None
            try:
                from rl_agent import get_rl_agent
                _rl_agent = get_rl_agent()
                _rl_state = _rl_agent.state_builder.build({
                    "rsi": getattr(signal, "rsi", 50),
                    "macd_hist": getattr(signal, "macd_hist", 0),
                    "vwap_deviation_pct": getattr(signal, "vwap_deviation_pct", 0),
                    "volume_ratio": getattr(signal, "volume_ratio", 1),
                    "mtf_alignment": getattr(signal, "mtf_alignment", "unknown"),
                    "patterns": getattr(signal, "patterns", []),
                    "session": getattr(signal, "session", "NORMAL"),
                    "nifty_trend": getattr(signal, "nifty_trend", "neutral"),
                })
            except Exception:
                pass

            conviction = calculate_conviction(
                signal_score      = getattr(signal, "signal_score", 0),
                claude_confidence = _review.get("confidence", 50),
                claude_approved   = _review.get("approved", True),
                rl_qtable         = _rl_agent,
                rl_state          = _rl_state,
                mtf_alignment     = getattr(signal, "mtf_alignment_dict",
                                           {"alignment_score": 50, "aligned_count": 2}),
                symbol            = signal.symbol,
                direction         = signal.direction,
                daily_pnl         = self.risk_manager.state.daily_pnl,
                daily_capital     = self.risk_manager.state.daily_capital,
            )
            old_qty = quantity
            quantity = apply_conviction_cap(
                quantity      = quantity,
                multiplier    = conviction.multiplier,
                entry_price   = signal.entry_price,
                stop_loss     = signal.stop_loss,
                daily_capital = self.risk_manager.state.daily_capital,
            )
            logger.info(
                f"[{format_ist_timestamp()}] CONVICTION: {signal.symbol} | "
                f"{conviction.reason} | qty {old_qty}→{quantity}"
            )
        except Exception as e:
            logger.debug(f"Dynamic leverage skipped: {e}")

        entry_price = round_to_tick_size(signal.entry_price)
        transaction_type = "BUY" if signal.direction == "LONG" else "SELL"

        logger.info(
            f"[{format_ist_timestamp()}] {'[LIVE]' if self.live_enabled else '[DRY]'} "
            f"Placing {transaction_type} {signal.symbol} x{quantity} @ ₹{entry_price:.2f} "
            f"| SL: ₹{signal.stop_loss:.2f} | Score: {signal.signal_score:.0f}"
        )

        if not self.live_enabled:
            # Dry run — simulate order
            fake_id = f"DRY_{signal.symbol}_{get_current_ist_time().strftime('%H%M%S')}"
            position = self._create_position(signal, quantity, entry_price, fake_id)
            self.risk_manager.add_position(position)
            self._log_to_db(signal, quantity, entry_price, fake_id, live=False)
            logger.info(
                f"[{format_ist_timestamp()}] [DRY RUN] Order simulated: {fake_id}"
            )
            return OrderResult(True, order_id=fake_id,
                               message=f"Dry run — {transaction_type} {quantity}x {signal.symbol}")

        # LIVE ORDER
        if not self._api:
            logger.warning(f"[{format_ist_timestamp()}] API not initialized — attempting re-init...")
            self._init_api()
            if not self._api:
                # This was the silent killer: auth not ready = zero orders placed
                msg = (
                    f"❌ Groww API not initialized for {signal.symbol}. "
                    "Token not yet obtained — will retry next cycle after 8:30 AM login."
                )
                logger.error(f"[{format_ist_timestamp()}] {msg}")
                try:
                    import requests as _req, config as _cfg
                    if _cfg.TELEGRAM_BOT_TOKEN and _cfg.TELEGRAM_CHAT_ID:
                        _req.post(
                            f"https://api.telegram.org/bot{_cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": _cfg.TELEGRAM_CHAT_ID, "parse_mode": "HTML",
                                  "text": f"⚠️ <b>Order Skipped — API Not Ready</b>\n"
                                          f"Symbol: {signal.symbol}\n"
                                          f"Reason: Groww token not obtained yet.\n"
                                          f"Bot will retry login in 5 minutes."},
                            timeout=5,
                        )
                except Exception:
                    pass
                return OrderResult(False, message=msg)

        try:
            # ── LIMIT-FIRST execution strategy (Grok: slippage eats 20-70% of edge) ──
            # 1. Place LIMIT at ask + slippage buffer (fills instantly on liquid NSE stocks)
            # 2. If not filled in 12s → adjust limit by another buffer, retry once
            # 3. If not filled in 24s → fall back to MARKET
            # This recovers ~0.1-0.2% per trade vs pure MARKET orders.
            try:
                from slippage_tracker import get_slippage_tracker
                _slip_tracker = get_slippage_tracker()
                _limit_price = _slip_tracker.adjust_limit_price(
                    signal.symbol, signal.direction, entry_price, retry=0
                )
                _use_limit = _limit_price > 0
            except Exception:
                _use_limit = False
                _limit_price = entry_price
                _slip_tracker = None

            filled_price = None
            order_id = None
            _used_order_type = "MARKET"

            for _attempt in range(3):   # 0=LIMIT, 1=LIMIT retry, 2=MARKET fallback
                if _attempt == 2 or not _use_limit:
                    _order_type  = "MARKET"
                    _price_param = {}
                    _used_order_type = "MARKET"
                else:
                    _order_type = "LIMIT"
                    _used_order_type = "LIMIT"
                    if _attempt == 1 and _slip_tracker:
                        _limit_price = _slip_tracker.adjust_limit_price(
                            signal.symbol, signal.direction, entry_price, retry=1
                        )
                    _price_param = {"price": _limit_price}

                order_params = {
                    "trading_symbol": signal.symbol,
                    "exchange": "NSE",
                    "segment": "CASH",
                    "transaction_type": transaction_type,
                    "quantity": quantity,
                    "product": "MIS",
                    "order_type": _order_type,
                    "validity": "DAY",
                    **_price_param,
                }

                response = self._call_place_order(order_params)
                logger.info(
                    f"[{format_ist_timestamp()}] place_order({_order_type}) "
                    f"attempt={_attempt+1}: {response}"
                )

                if not (response and (response.get("order_id") or response.get("id"))):
                    if _attempt < 2:
                        continue
                    break

                order_id = str(response.get("order_id") or response.get("id"))

                if _order_type == "MARKET":
                    filled_price = self._wait_for_fill(
                        order_id, entry_price, timeout=60, is_market_order=True
                    )
                    if filled_price is None:
                        from data_fetch_groww import get_data_fetcher
                        q = get_data_fetcher().get_quote(signal.symbol)
                        filled_price = q.get("ltp", entry_price) if q else entry_price
                        logger.warning(
                            f"[{format_ist_timestamp()}] ⚠️ {order_id}: fill status not "
                            f"confirmed — using LTP ₹{filled_price:.2f}. CHECK GROWW APP."
                        )
                    break
                else:
                    # LIMIT: wait 12 seconds for fill
                    filled_price = self._wait_for_fill(
                        order_id, _limit_price, timeout=12, is_market_order=False
                    )
                    if filled_price:
                        break   # Filled!
                    else:
                        # Not filled — cancel and retry with wider limit or market
                        try:
                            self._cancel_order(order_id)
                        except Exception:
                            pass
                        logger.info(
                            f"[{format_ist_timestamp()}] LIMIT not filled in 12s — "
                            f"{'retrying wider' if _attempt == 0 else 'falling back to MARKET'}"
                        )
                        order_id = None
                        continue

            if not order_id or filled_price is None:
                return OrderResult(False, message="Order placement failed after 3 attempts")

                # ── Record slippage for adaptive limit pricing ────────────────
                if _slip_tracker:
                    try:
                        _slip_tracker.record_fill(
                            symbol=signal.symbol, direction=signal.direction,
                            signal_price=entry_price, fill_price=filled_price,
                            quantity=quantity, order_type=_used_order_type,
                        )
                    except Exception:
                        pass

                position = self._create_position(signal, quantity, filled_price, order_id)

                # ── Place SL order immediately after entry (critical safety net) ──
                # This ensures Groww's exchange-side SL fires even if the bot crashes.
                sl_order_id = self._place_sl_order(
                    symbol=signal.symbol,
                    direction=signal.direction,
                    quantity=quantity,
                    sl_price=signal.stop_loss,
                    entry_price=filled_price,
                )
                if sl_order_id:
                    position.sl_order_id = sl_order_id
                    logger.info(
                        f"[{format_ist_timestamp()}] ✅ SL ORDER: {sl_order_id} | "
                        f"{signal.symbol} SL @ ₹{signal.stop_loss:.2f}"
                    )
                else:
                    logger.warning(
                        f"[{format_ist_timestamp()}] ⚠️ SL ORDER FAILED for {signal.symbol} "
                        f"@ ₹{signal.stop_loss:.2f} — monitor manually! "
                        f"Bot will manage SL via polling but exchange-side protection is missing."
                    )

                self.risk_manager.add_position(position)
                self._log_to_db(signal, quantity, filled_price, order_id, live=True)

                logger.info(
                    f"[{format_ist_timestamp()}] ✅ ORDER PLACED: {order_id} | "
                    f"{transaction_type} {signal.symbol} x{quantity} "
                    f"@ ₹{filled_price:.2f} (signal ₹{entry_price:.2f})"
                )
                # ── Trade entry alert ─────────────────────────────────────────
                cap   = self.risk_manager.state.daily_capital
                direction = 'BUY (LONG)' if transaction_type == 'BUY' else 'SELL (SHORT)'
                _notify(
                    f"TRADE ENTRY — {signal.symbol} ({direction})",
                    f"Qty: {quantity} shares @ Rs.{filled_price:.2f}\n"
                    f"Capital deployed: Rs.{quantity * filled_price:,.0f} (5x MIS)\n"
                    f"Margin used: Rs.{quantity * filled_price / 5:,.0f} "
                    f"(balance Rs.{cap:,.0f})\n"
                    f"SL: Rs.{signal.stop_loss:.2f}  |  "
                    f"T1: Rs.{signal.target_1:.2f}  |  T2: Rs.{signal.target_2:.2f}\n"
                    f"Risk: Rs.{abs(filled_price - signal.stop_loss) * quantity:,.0f} "
                    f"({abs(filled_price - signal.stop_loss) / filled_price * 100:.2f}%)\n"
                    f"Score: {signal.signal_score:.0f}  |  Order: {order_id}"
                )
                return OrderResult(True, order_id=order_id,
                                   message=f"Filled: {order_id}", raw=response)
            else:
                logger.error(
                    f"[{format_ist_timestamp()}] Order placement failed: {response}"
                )
                return OrderResult(False, message=f"API error: {response}")

        except GrowwIPBlockedError as ip_err:
            msg = (
                "🚨 GROWW IP BLOCKED — Orders cannot be placed!\n\n"
                "Your VPS IP is not whitelisted in Groww.\n\n"
                "Fix:\n"
                "1. Go to Groww → Profile → Developer API Settings\n"
                f"2. Add your VPS IP ({_get_outbound_ip()}) to the allowed list\n"
                "3. Save and wait 1-2 minutes for it to take effect\n\n"
                f"Raw error: {ip_err}"
            )
            logger.error(f"[{format_ist_timestamp()}] {msg}")
            # Alert once per hour to avoid spam
            global _ip_block_last_alerted
            now = get_current_ist_time()
            if (_ip_block_last_alerted is None or
                    (now - _ip_block_last_alerted).total_seconds() > 3600):
                _ip_block_last_alerted = now
                _notify("GROWW IP BLOCKED — Orders cannot be placed!", msg)
            return OrderResult(False, message="IP not whitelisted on Groww")

        except Exception as e:
            logger.error(
                f"[{format_ist_timestamp()}] place_entry_order exception: {e}"
            )
            return OrderResult(False, message=str(e))

    # --------------------------------------------------------
    # EXIT ORDERS
    # --------------------------------------------------------

    def place_exit_order(
        self,
        symbol: str,
        quantity: int,
        direction: str,
        reason: str = "Signal exit",
        use_market_order: bool = False,
    ) -> OrderResult:
        """
        Place exit (square-off) order for an open position.
        Uses MARKET order for urgent exits (SL hit, kill switch, EOD).
        """
        exit_type = "SELL" if direction == "LONG" else "BUY"
        order_type = "MARKET" if use_market_order else "LIMIT"

        if not self.live_enabled:
            from data_fetch_groww import get_data_fetcher
            fetcher = get_data_fetcher()
            quote = fetcher.get_quote(symbol)
            exit_price = quote.get("ltp", 0) if quote else 0
            trade = self.risk_manager.close_position(symbol, exit_price, reason)
            if trade:
                self._update_db_exit(symbol, exit_price, reason)
                try:
                    from rl_agent import LakshKingRL
                    q = fetcher.get_quote(symbol) or {}
                    LakshKingRL.close(symbol, trade.pnl, reason, {
                        "rsi": q.get("rsi", 50), "macd_hist": 0,
                        "vwap_deviation_pct": 0, "volume_ratio": 1,
                        "mtf_alignment": "unknown", "patterns": [],
                        "session": "NORMAL", "nifty_trend": "neutral",
                        "signal_score": 0,
                    })
                except Exception:
                    pass
            fake_id = f"DRY_EXIT_{symbol}_{get_current_ist_time().strftime('%H%M%S')}"
            logger.info(f"[{format_ist_timestamp()}] [DRY RUN] Exit simulated: {symbol}")
            return OrderResult(True, order_id=fake_id,
                               message=f"Dry exit {symbol} — {reason}")

        if not self._api:
            self._init_api()

        try:
            from data_fetch_groww import get_data_fetcher
            fetcher = get_data_fetcher()
            quote = fetcher.get_quote(symbol)
            exit_price = round_to_tick_size(quote.get("ltp", 0)) if quote else 0

            params = {
                "trading_symbol": symbol,  # Groww SDK uses trading_symbol, not symbol
                "exchange": "NSE",
                "segment": "CASH",         # Required by Groww SDK for equities
                "transaction_type": exit_type,
                "quantity": quantity,
                "product": "MIS",
                "order_type": order_type,
                "validity": "DAY",
            }
            if order_type == "LIMIT" and exit_price:
                params["price"] = exit_price

            response = self._call_place_order(params)

            if response and (response.get("order_id") or response.get("id")):
                order_id = str(response.get("order_id") or response.get("id"))
                trade = self.risk_manager.close_position(symbol, exit_price, reason)
                if trade:
                    self._update_db_exit(symbol, exit_price, reason)
                    try:
                        from rl_agent import LakshKingRL
                        LakshKingRL.close(symbol, trade.pnl, reason, {
                            "rsi": 50, "macd_hist": 0,
                            "vwap_deviation_pct": 0, "volume_ratio": 1,
                            "mtf_alignment": "unknown", "patterns": [],
                            "session": "NORMAL", "nifty_trend": "neutral",
                            "signal_score": 0,
                        })
                    except Exception:
                        pass
                logger.info(
                    f"[{format_ist_timestamp()}] ✅ EXIT ORDER: {symbol} x{quantity} | {reason}"
                )
                # ── Trade exit alert ──────────────────────────────────────────
                pnl    = trade.pnl     if trade else 0.0
                entry  = trade.entry_price if trade else 0.0
                result = "PROFIT" if pnl >= 0 else "LOSS"
                _notify(
                    f"TRADE EXIT — {symbol} ({result}: {'+'if pnl>=0 else ''}Rs.{pnl:,.0f})",
                    f"Reason: {reason}\n"
                    f"Entry: Rs.{entry:.2f}  |  Exit: Rs.{exit_price:.2f}\n"
                    f"Qty: {quantity}  |  P&L: {'+'if pnl>=0 else ''}Rs.{pnl:,.0f}\n"
                    f"Order: {order_id}"
                )
                return OrderResult(True, order_id=order_id, raw=response)
            else:
                # Try market order fallback
                params["order_type"] = "MARKET"
                params.pop("price", None)
                response2 = self._call_place_order(params)
                if response2 and (response2.get("order_id") or response2.get("id")):
                    order_id = str(response2.get("order_id") or response2.get("id"))
                    self.risk_manager.close_position(symbol, exit_price, reason + " (MARKET fallback)")
                    return OrderResult(True, order_id=order_id, raw=response2)
                return OrderResult(False, message=f"Exit failed: {response}")

        except GrowwIPBlockedError as ip_err:
            logger.error(
                f"[{format_ist_timestamp()}] EXIT BLOCKED — IP not whitelisted: {ip_err}. "
                f"Add VPS IP ({_get_outbound_ip()}) to Groww Developer API Settings."
            )
            return OrderResult(False, message="IP not whitelisted — exit blocked")
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] place_exit_order error: {e}")
            return OrderResult(False, message=str(e))

    # --------------------------------------------------------
    # MODIFY STOP LOSS
    # --------------------------------------------------------

    def modify_stop_loss(self, symbol: str, new_sl: float) -> OrderResult:
        """Modify the SL for an open position's SL order."""
        if symbol not in self.risk_manager.state.positions:
            return OrderResult(False, message=f"No position found for {symbol}")

        pos = self.risk_manager.state.positions[symbol]
        pos.stop_loss = new_sl
        if pos.trailing_active:
            pos.trailing_stop = new_sl

        if not self.live_enabled:
            logger.info(f"[{format_ist_timestamp()}] [DRY] SL modified: {symbol} → ₹{new_sl:.2f}")
            return OrderResult(True, message=f"Dry: SL updated to ₹{new_sl:.2f}")

        # Update SL order on Groww: cancel old SL, place new one at updated price
        if pos.sl_order_id and self._api:
            try:
                # Cancel the existing SL order
                self._cancel_order(pos.sl_order_id)
            except Exception:
                pass

        # Always place a fresh SL order at the new level (trailing stop update)
        if self._api:
            new_sl_order_id = self._place_sl_order(
                symbol=symbol,
                direction=pos.direction,
                quantity=pos.quantity,
                sl_price=new_sl,
                entry_price=pos.entry_price,
            )
            if new_sl_order_id:
                pos.sl_order_id = new_sl_order_id
                logger.info(
                    f"[{format_ist_timestamp()}] SL updated: {symbol} → ₹{new_sl:.2f} "
                    f"(order: {new_sl_order_id})"
                )
                return OrderResult(True, order_id=new_sl_order_id)
            else:
                logger.warning(
                    f"[{format_ist_timestamp()}] SL order replace failed: "
                    f"{symbol} → ₹{new_sl:.2f} — local state updated, monitor manually"
                )

        return OrderResult(True, message=f"SL updated locally to ₹{new_sl:.2f}")

    # --------------------------------------------------------
    # SQUARE-OFF ALL (EOD / KILL SWITCH)
    # --------------------------------------------------------

    def square_off_all(self, reason: str = "EOD square-off") -> List[OrderResult]:
        """
        Square off ALL open positions immediately.
        Used for EOD (3:20 PM IST) and kill switch.
        Always uses MARKET orders for guaranteed execution.
        """
        logger.warning(
            f"[{format_ist_timestamp()}] 🔴 SQUARE OFF ALL POSITIONS: {reason}"
        )
        results = []
        positions = list(self.risk_manager.state.positions.values())

        for pos in positions:
            result = self.place_exit_order(
                symbol=pos.symbol,
                quantity=pos.quantity,
                direction=pos.direction,
                reason=reason,
                use_market_order=True,  # MARKET for guaranteed fill
            )
            results.append(result)
            if not result.success:
                logger.error(
                    f"[{format_ist_timestamp()}] ❌ Failed to exit {pos.symbol}: "
                    f"{result.message}"
                )
        logger.warning(
            f"[{format_ist_timestamp()}] Square-off complete: "
            f"{sum(1 for r in results if r.success)}/{len(results)} successful"
        )
        return results

    # --------------------------------------------------------
    # HELPERS
    # --------------------------------------------------------
    # ORDER FILL CONFIRMATION
    # --------------------------------------------------------

    def _wait_for_fill(self, order_id: str, expected_price: float,
                       timeout: int = 60,
                       is_market_order: bool = False) -> Optional[float]:
        """
        Poll Groww order status until FILLED or timeout.
        Returns actual fill price on success, None if status not confirmed.

        MARKET orders fill in <5s. LIMIT orders may take longer.
        For MARKET orders we do NOT cancel on timeout — the order is already at
        the exchange and will fill. Caller handles the None case by using LTP.

        Groww order statuses:
          Pending : PLACED, OPEN, PENDING, TRANSIT, TRIGGER_PENDING, OPEN_PENDING
          Filled  : COMPLETE, FILLED, TRADED, EXECUTED, PARTIAL_EXECUTED
          Terminal: CANCELLED, REJECTED, EXPIRED, FAILED
        """
        import time as _time
        deadline = _time.time() + timeout
        poll_interval = 2  # Check every 2 seconds

        # Status sets
        FILL_STATUSES     = {"COMPLETE", "FILLED", "TRADED", "EXECUTED",
                             "PARTIAL_EXECUTED", "FULL", "DONE", "SUCCESS"}
        TERMINAL_STATUSES = {"CANCELLED", "REJECTED", "EXPIRED", "FAILED",
                             "CANCEL", "REJECT"}

        while _time.time() < deadline:
            if not self._api:
                logger.warning(f"[{format_ist_timestamp()}] API lost during fill-wait for {order_id}")
                break
            try:
                status_resp = self._api.get_order(order_id=order_id)
                if not status_resp:
                    _time.sleep(poll_interval)
                    continue

                # Unwrap envelope if needed
                data = status_resp
                for env_key in ("data", "payload", "result"):
                    if env_key in status_resp and isinstance(status_resp[env_key], dict):
                        data = status_resp[env_key]
                        break

                raw_status = (
                    data.get("status") or
                    data.get("order_status") or
                    data.get("orderStatus") or
                    status_resp.get("status") or
                    ""
                )
                status = str(raw_status).upper().strip()

                logger.debug(f"Order {order_id} status: {status!r}")

                if status in FILL_STATUSES:
                    fill_price = (
                        data.get("average_price") or
                        data.get("avg_price") or
                        data.get("averagePrice") or
                        data.get("filled_price") or
                        status_resp.get("average_price") or
                        expected_price
                    )
                    return float(fill_price)

                if status in TERMINAL_STATUSES:
                    reason = (
                        data.get("message") or data.get("reason") or
                        data.get("errorMessage") or data.get("reject_reason") or
                        "No reason provided"
                    )
                    logger.warning(
                        f"[{format_ist_timestamp()}] Order {order_id} {status}: {reason}"
                    )
                    r = str(reason).lower()
                    if "circuit"  in r:
                        logger.error(f"⛔ CIRCUIT LIMIT hit: {order_id}")
                    elif "margin" in r or "fund" in r:
                        logger.error(f"⛔ INSUFFICIENT MARGIN: {order_id} — reduce qty")
                    elif "short"  in r or "sell" in r:
                        logger.error(f"⛔ SHORT REJECTED: {order_id} — check F&O eligibility")
                    return None

                # Still pending (PLACED / OPEN / TRANSIT / etc.) — keep polling
                _time.sleep(poll_interval)

            except Exception as e:
                logger.debug(f"Order status check error: {e}")
                _time.sleep(poll_interval)

        # Timeout
        order_type_label = "MARKET" if is_market_order else "LIMIT"
        logger.warning(
            f"[{format_ist_timestamp()}] {order_type_label} order {order_id} "
            f"fill not confirmed in {timeout}s"
        )
        return None  # Caller decides whether to cancel or trust the fill

    def _cancel_order(self, order_id: str) -> bool:
        """Cancel an unfilled order."""
        try:
            resp = self._api.cancel_order(order_id=order_id)
            logger.info(f"[{format_ist_timestamp()}] Order {order_id} cancelled")
            return bool(resp)
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Cancel order {order_id} failed: {e}")
            return False

    # --------------------------------------------------------

    def _place_sl_order(
        self,
        symbol: str,
        direction: str,
        quantity: int,
        sl_price: float,
        entry_price: float,
    ) -> str:
        """
        Place a Stop-Loss Market (SLM) order on Groww immediately after entry.
        This is the exchange-side safety net — fires even if the bot crashes.

        SL direction is the OPPOSITE of entry:
          LONG entry → SL is a SELL stop (triggers when price drops to sl_price)
          SHORT entry → SL is a BUY stop (triggers when price rises to sl_price)

        Returns the sl_order_id string, or "" on failure.
        """
        if not self.live_enabled or not self._api:
            return ""

        sl_transaction = "SELL" if direction == "LONG" else "BUY"

        # Add a small buffer to trigger price to avoid premature triggers:
        # LONG SL: trigger slightly below sl_price (0.05% buffer)
        # SHORT SL: trigger slightly above sl_price (0.05% buffer)
        buffer = sl_price * 0.0005
        if direction == "LONG":
            trigger_price = round_to_tick_size(sl_price - buffer)
        else:
            trigger_price = round_to_tick_size(sl_price + buffer)

        sl_params = {
            "trading_symbol": symbol,
            "exchange":       "NSE",
            "segment":        "CASH",
            "transaction_type": sl_transaction,
            "quantity":       quantity,
            "product":        "MIS",
            "order_type":     "SLM",       # Stop-Loss Market — triggers and fills at market
            "trigger_price":  trigger_price,
            "validity":       "DAY",
        }

        try:
            resp = self._call_place_order(sl_params)
            if resp and (resp.get("order_id") or resp.get("id")):
                return str(resp.get("order_id") or resp.get("id"))
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] SL order placement error: {e}")

        # Fallback: try SL-Limit (price = 0.1% worse than trigger for guaranteed fill)
        try:
            if direction == "LONG":
                limit_price = round_to_tick_size(trigger_price * 0.999)
            else:
                limit_price = round_to_tick_size(trigger_price * 1.001)
            sl_params["order_type"] = "SL"
            sl_params["price"] = limit_price
            resp2 = self._call_place_order(sl_params)
            if resp2 and (resp2.get("order_id") or resp2.get("id")):
                return str(resp2.get("order_id") or resp2.get("id"))
        except Exception as e2:
            logger.warning(f"[{format_ist_timestamp()}] SL-Limit fallback error: {e2}")

        return ""

    def _create_position(
        self, signal: TradeSignal, quantity: int,
        entry_price: float, order_id: str
    ) -> Position:
        return Position(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=signal.stop_loss,
            target_1=signal.target_1,
            target_2=signal.target_2,
            atr=signal.atr,
            order_id=order_id,
            quality_grade=getattr(signal, "quality_grade", "B"),
            size_multiplier=getattr(signal, "size_multiplier", 1.0),
        )

    def _log_to_db(
        self, signal: TradeSignal, quantity: int,
        entry_price: float, order_id: str, live: bool
    ):
        """Log trade entry to SEBI-compliant SQLite journal."""
        try:
            conn = sqlite3.connect(TRADE_DB_PATH)
            conn.execute("""
                INSERT INTO execution_trades
                (order_id, symbol, direction, quantity, entry_price,
                 stop_loss, target_1, target_2, product, signal_score,
                 patterns, entry_time, live_trade, created_at, atr)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                order_id, signal.symbol, signal.direction, quantity, entry_price,
                signal.stop_loss, signal.target_1, signal.target_2, "MIS",
                signal.signal_score, str(signal.patterns),
                format_ist_timestamp(), int(live),
                format_ist_timestamp(), signal.atr,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"DB log error: {e}")

    def _update_db_exit(self, symbol: str, exit_price: float, reason: str):
        """Update trade journal with exit details."""
        try:
            pos_closed = None
            for t in self.risk_manager.state.closed_trades:
                if t.get("symbol") == symbol:
                    pos_closed = t
                    break
            if not pos_closed:
                return
            pnl = pos_closed.get("pnl", 0)
            conn = sqlite3.connect(TRADE_DB_PATH)
            conn.execute("""
                UPDATE execution_trades SET exit_price=?, pnl=?, pnl_pct=?,
                exit_time=?, exit_reason=?
                WHERE symbol=? AND exit_price IS NULL
                ORDER BY id DESC LIMIT 1
            """, (exit_price, pnl, pos_closed.get("pnl_pct", 0),
                  format_ist_timestamp(), reason, symbol))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"DB exit update error: {e}")
