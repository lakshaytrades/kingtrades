"""
crypto_engine.py — 24/7 Crypto Trading Orchestrator (PROFIT-FIRST after -$4K loss event)

CRITICAL RISK RULES:
  - Only BTC/ETH/SOL — liquid pairs, tight spreads
  - Max $500 per trade, 0.5% risk per trade
  - 2% daily loss limit on crypto pool
  - US_NIGHT session (21:00-00:00 UTC) COMPLETELY DISABLED
  - 2 consecutive losses → 3-hour pause
  - BTC/ETH/SOL only — no DOGE/AVAX/LINK
  - Hard MTF gates: 1H must agree, 4H must not oppose
  - Min signal score 75 (was 68)

Features:
  - 24/7 operation (session-aware, US_NIGHT off)
  - BTC correlation filter (suppress alts in BTC downtrend)
  - Fear & Greed index integration
  - Whale volume detection
  - ATR-based stops + T1/T2/Runner partial exits (40%/30%/30%)
  - UTC midnight reset (24/7 = no EOD, just daily reset)
  - Telegram alerts for every entry/exit/partial

Integration:
  - Started by main.py as daemon thread: crypto_engine.start()
  - Shares same Alpaca API keys
  - Separate capital pool (20% of account by default)
  - Independent risk management from stock positions
"""

import logging
import threading
import time
from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo

import crypto_config as ccfg
from crypto_signals import scan_crypto_watchlist, CryptoSignal
from crypto_executor import get_crypto_executor, CryptoExecutor
from utils import format_ist_timestamp

logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")
ET  = ZoneInfo("America/New_York")


class CryptoEngine:
    """
    24/7 crypto trading engine. Runs scan-execute-manage loop every 60 seconds.
    Manages its own capital pool, positions, and risk state.
    """

    def __init__(self, alerter=None, live_enabled: bool = False):
        self.alerter      = alerter
        self.live_enabled = live_enabled
        self.executor: CryptoExecutor = get_crypto_executor(live_enabled)
        self._running    = False
        self._thread: Optional[threading.Thread] = None
        self._last_reset_date: Optional[date] = None
        self._last_heartbeat_hour = -1
        self._daily_start_capital: float = 0.0
        self._scan_count: int = 0
        self._total_trades: int = 0

    def start(self) -> None:
        """Start crypto engine in a daemon background thread."""
        if not ccfg.CRYPTO_ENABLED:
            logger.info("CryptoEngine: CRYPTO_ENABLED=False — not starting")
            return

        self._running = True
        self._daily_start_capital = self._get_crypto_capital()
        self.executor.set_daily_capital(self._daily_start_capital)

        # Reconcile any existing broker positions before starting the loop.
        # This closes oversized pre-fix positions and reloads untracked ones
        # so their stop-losses are monitored from the first scan cycle.
        try:
            actions = self.executor.reconcile_broker_positions()
            if actions:
                logger.info(f"[CRYPTO] Startup reconcile: {actions} position(s) handled")
                self._send_alert(
                    f"🔄 <b>Startup Position Audit</b>\n"
                    f"Reconciled {actions} broker position(s).\n"
                    f"Oversized pre-fix positions closed. All remaining positions now monitored."
                )
        except Exception as e:
            logger.warning(f"Startup reconcile failed: {e}")

        self._thread  = threading.Thread(
            target=self._run_loop, name="CryptoEngine", daemon=True
        )
        self._thread.start()
        logger.info(
            f"[{format_ist_timestamp()}] CryptoEngine started "
            f"({'LIVE' if self.live_enabled else 'PAPER'}) | "
            f"Pairs: {', '.join(ccfg.CRYPTO_SYMBOLS)} | "
            f"Pool: {ccfg.CRYPTO_CAPITAL_PCT_OF_TOTAL:.0f}% of account"
        )
        self._send_alert(
            f"🪙 <b>Crypto Engine Started</b>\n"
            f"<code>{format_ist_timestamp()}</code>\n\n"
            f"Mode: <b>{'⚡ LIVE' if self.live_enabled else '🔒 PAPER'}</b>\n"
            f"Pairs: <b>{', '.join(ccfg.CRYPTO_SYMBOLS)}</b>\n"
            f"Capital pool: <b>{ccfg.CRYPTO_CAPITAL_PCT_OF_TOTAL:.0f}%</b> of account\n"
            f"Max positions: <b>{ccfg.CRYPTO_MAX_POSITIONS}</b>\n"
            f"Risk/trade: <b>{ccfg.CRYPTO_MAX_RISK_PCT}%</b>\n"
            f"Daily loss limit: <b>{ccfg.CRYPTO_DAILY_LOSS_PCT}%</b>\n\n"
            f"Strategy: MTF Momentum + VWAP + BB + F&G + BTC Correlation\n"
            f"Running 24/7 — no market hours"
        )

    def stop(self) -> None:
        """Stop the engine gracefully."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info(f"[{format_ist_timestamp()}] CryptoEngine stopped")

    def get_status(self) -> dict:
        """Public status summary for heartbeat / Telegram /status command."""
        exe_status = self.executor.get_status()
        return {
            "running":        self._running,
            "live":           self.live_enabled,
            "positions":      exe_status["positions"],
            "daily_pnl":      exe_status["daily_pnl"],
            "daily_trades":   exe_status["daily_trades"],
            "consec_losses":  exe_status["consecutive_losses"],
            "paused":         exe_status["paused"],
            "scan_count":     self._scan_count,
            "total_trades":   self._total_trades,
        }

    # ─────────────────────────────────────────────────────────────────────
    # MAIN LOOP
    # ─────────────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """The 24/7 scan-execute-manage loop."""
        while self._running:
            try:
                now_utc = datetime.now(UTC)

                # UTC midnight reset
                today = now_utc.date()
                if self._last_reset_date != today:
                    self._daily_reset(today)

                # Hourly heartbeat
                if now_utc.hour != self._last_heartbeat_hour:
                    self._last_heartbeat_hour = now_utc.hour
                    self._send_heartbeat()

                # Emergency regime check — close longs in violent bear market
                try:
                    self._check_regime_emergency()
                except Exception as _e:
                    logger.debug(f"regime emergency check: {_e}")

                # Update open positions (SL hits, T1/T2, trailing stop)
                self._update_positions()

                # Check capital pool
                capital = self._get_crypto_capital()
                if capital < ccfg.CRYPTO_MIN_NOTIONAL_USD * 3:
                    logger.warning(
                        f"[{format_ist_timestamp()}] Crypto capital too low: "
                        f"${capital:.2f} — skipping scan"
                    )
                    time.sleep(ccfg.CRYPTO_SCAN_INTERVAL)
                    continue

                # Check if paused or daily loss limit hit
                status = self.executor.get_status()
                if status["paused"]:
                    logger.debug(f"[{format_ist_timestamp()}] Crypto engine paused — skipping scan")
                    time.sleep(ccfg.CRYPTO_SCAN_INTERVAL)
                    continue

                # Scan for signals
                signals = scan_crypto_watchlist(
                    symbols=ccfg.CRYPTO_SYMBOLS,
                    total_crypto_capital=capital,
                )
                self._scan_count += 1

                # Filter by executor state (positions, daily limits)
                for sig in signals[:3]:   # max 3 new signals per scan cycle
                    allowed, reason = self.executor.can_trade(sig.symbol)
                    if not allowed:
                        logger.debug(f"[CRYPTO] {sig.symbol} blocked: {reason}")
                        continue

                    # Execute
                    result = self.executor.place_entry(sig)
                    if result.success:
                        self._total_trades += 1
                        self._send_entry_alert(sig, result)

            except Exception as e:
                import traceback
                logger.error(
                    f"[{format_ist_timestamp()}] CryptoEngine loop error: {e}\n"
                    f"{traceback.format_exc()}"
                )

            time.sleep(ccfg.CRYPTO_SCAN_INTERVAL)

    def _update_positions(self) -> None:
        """Update all open positions, handle exits, send alerts."""
        if not self.executor.positions:
            return

        closed = self.executor.update_positions()
        for symbol in closed:
            logger.info(f"[{format_ist_timestamp()}] [CRYPTO] Position closed: {symbol}")
            daily_pnl = self.executor._daily_pnl
            self._send_alert(
                f"🔔 <b>Crypto Position Closed</b>: {symbol}\n"
                f"Daily P&L: <b>${daily_pnl:+,.2f}</b>"
            )

    def _daily_reset(self, today: date) -> None:
        """Reset daily state at UTC midnight for 24/7 crypto."""
        self._last_reset_date = today
        prev_pnl = self.executor._daily_pnl
        self.executor.reset_daily()
        self._daily_start_capital = self._get_crypto_capital()
        self.executor.set_daily_capital(self._daily_start_capital)

        # Re-reconcile at each day boundary to catch any positions opened
        # since the last reconcile (e.g. by a parallel process or manual trade)
        try:
            self.executor.reconcile_broker_positions()
        except Exception as e:
            logger.debug(f"daily_reset reconcile: {e}")

        logger.info(
            f"[{format_ist_timestamp()}] Crypto daily reset | "
            f"Yesterday P&L: ${prev_pnl:+,.2f} | "
            f"New capital pool: ${self._daily_start_capital:,.2f}"
        )
        self._send_alert(
            f"📅 <b>Crypto Day Reset</b> — {today.strftime('%Y-%m-%d')}\n"
            f"Yesterday P&L: <b>${prev_pnl:+,.2f}</b>\n"
            f"New capital pool: <b>${self._daily_start_capital:,.2f}</b>\n"
            f"Strategy continues 24/7"
        )

    def _check_regime_emergency(self) -> None:
        """Close all LONG positions if BTC enters VOLATILE_BEAR regime."""
        if not self.executor.positions:
            return
        from crypto_data import get_crypto_bars
        from crypto_signals import _compute_indicators
        from crypto_regime import detect_regime, Regime

        btc_df = get_crypto_bars("BTC/USD", "15Min", limit=80)
        if btc_df is None or len(btc_df) < 30:
            return
        btc_ind = _compute_indicators(btc_df)
        if not btc_ind:
            return

        regime = detect_regime(btc_df, btc_ind)
        if regime == Regime.VOLATILE_BEAR:
            long_syms = [
                sym for sym, pos in self.executor.positions.items()
                if pos.direction == "LONG"
            ]
            if long_syms:
                logger.warning(
                    f"[CRYPTO] VOLATILE_BEAR regime detected — emergency closing "
                    f"{len(long_syms)} LONG position(s)"
                )
                self._send_alert(
                    f"🚨 <b>VOLATILE BEAR EMERGENCY</b>\n"
                    f"BTC regime = VOLATILE_BEAR\n"
                    f"Closing {len(long_syms)} LONG position(s): {', '.join(long_syms)}\n"
                    f"Capital preservation priority"
                )
                for sym in long_syms:
                    self.executor.place_exit(sym, reason="VOLATILE_BEAR_EMERGENCY")

    def _send_heartbeat(self) -> None:
        """Hourly status update."""
        status = self.get_status()
        now_str = datetime.now(ET).strftime("%H:%M ET")

        # Compute current BTC regime for heartbeat
        try:
            from crypto_data import get_crypto_bars
            from crypto_signals import _compute_indicators
            from crypto_regime import detect_regime
            btc_df = get_crypto_bars("BTC/USD", "15Min", limit=80)
            if btc_df is not None:
                btc_ind = _compute_indicators(btc_df)
                regime_str = detect_regime(btc_df, btc_ind).value if btc_ind else "UNKNOWN"
            else:
                regime_str = "UNKNOWN"
        except Exception:
            regime_str = "UNKNOWN"

        pos_lines = []
        for sym, pos in self.executor.positions.items():
            from crypto_data import get_crypto_quote
            q   = get_crypto_quote(sym)
            ltp = q.get("ltp", pos.entry_price)
            if pos.entry_price > 0:
                remaining = pos.remaining_qty if pos.remaining_qty > 0 else pos.filled_qty
                unrealised = (ltp - pos.entry_price) * remaining
                if pos.direction == "SHORT":
                    unrealised = -unrealised
                total_pnl = round(pos.running_pnl + unrealised, 2)
                t_flags = ("T1✓" if pos.t1_done else "") + (" T2✓" if pos.t2_done else "")
                pos_lines.append(
                    f"  {sym} {pos.direction} @ ${pos.entry_price:,.2f} → "
                    f"${ltp:,.2f} | ${total_pnl:+.0f} {t_flags}"
                )

        pos_text = "\n".join(pos_lines) if pos_lines else "  No open positions"

        self._send_alert(
            f"🪙 <b>Crypto Heartbeat</b> — {now_str}\n\n"
            f"Daily P&L: <b>${status['daily_pnl']:+,.2f}</b>\n"
            f"Trades today: <b>{status['daily_trades']}</b>\n"
            f"Open positions: <b>{status['positions']}</b>\n"
            f"Regime: <b>{regime_str}</b>\n"
            f"{'⏸ PAUSED' if status['paused'] else '▶ ACTIVE'}\n\n"
            f"<b>Positions:</b>\n{pos_text}"
        )

    def _send_entry_alert(self, sig: CryptoSignal, result) -> None:
        """Send Telegram alert when a crypto trade is entered."""
        fng_label = (
            "😱 Extreme Fear" if sig.fng_index <= 25
            else "😰 Fear" if sig.fng_index <= 40
            else "😁 Greed" if sig.fng_index >= 65
            else "🤑 Extreme Greed" if sig.fng_index >= 80
            else "😐 Neutral"
        )
        emoji = "🟢" if sig.direction == "LONG" else "🔴"
        self._send_alert(
            f"{emoji} <b>Crypto {sig.direction}</b>: {sig.symbol}\n"
            f"<code>{format_ist_timestamp()}</code>\n\n"
            f"Entry:  <b>${sig.entry_price:,.4f}</b>\n"
            f"Stop:   <b>${sig.stop_loss:,.4f}</b>\n"
            f"T1:     <b>${sig.target_1:,.4f}</b>\n"
            f"T2:     <b>${sig.target_2:,.4f}</b>\n"
            f"Runner: <b>${sig.target_runner:,.4f}</b>\n\n"
            f"Notional: <b>${result.notional:,.0f}</b>\n"
            f"Score:    <b>{sig.signal_score:.0f}/100</b> ({sig.quality_grade})\n"
            f"R:R:      <b>{sig.risk_reward:.1f}:1</b>\n\n"
            f"F&G: {fng_label} ({sig.fng_index})\n"
            f"BTC 1h: {sig.btc_change_1h:+.1f}%\n"
            f"Session: {sig.session}\n\n"
            f"<i>{sig.rationale[:120]}</i>"
        )

    def _get_crypto_capital(self) -> float:
        """Get available crypto capital from account or config."""
        if ccfg.CRYPTO_CAPITAL_USD > 0:
            return ccfg.CRYPTO_CAPITAL_USD

        try:
            from crypto_data import get_crypto_account_info
            acct = get_crypto_account_info()
            equity = acct.get("equity", 0.0)
            if equity > 0:
                return equity * ccfg.CRYPTO_CAPITAL_PCT_OF_TOTAL / 100
        except Exception as e:
            logger.debug(f"get_crypto_capital: {e}")

        return 1000.0   # fallback

    def _send_alert(self, text: str) -> None:
        if self.alerter:
            try:
                self.alerter.send_text(text)
            except Exception as e:
                logger.debug(f"crypto alert failed: {e}")
        else:
            logger.info(f"[CRYPTO ALERT] {text[:100]}")


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_engine: Optional[CryptoEngine] = None


def get_crypto_engine(alerter=None, live_enabled: bool = False) -> CryptoEngine:
    global _engine
    if _engine is None:
        _engine = CryptoEngine(alerter=alerter, live_enabled=live_enabled)
    return _engine
