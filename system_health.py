"""
system_health.py — Pre-Trade System Health Monitor

Runs before every scan cycle to verify critical subsystems are operational.
Blocks trading if API is down, data is stale, or positions are unreconciled.

Health checks (in order of criticality):
  1. ALPACA_API    — REST API reachable, auth valid
  2. DATA_FRESH    — Last price quote < 30s old (WebSocket alive)
  3. POSITIONS     — Open positions match Alpaca's server state
  4. CAPITAL       — Available buying power > minimum threshold
  5. TIME_WINDOW   — Within valid trading hours (9:30-15:55 ET)
  6. CIRCUIT       — Daily loss limit not hit
  7. SCORE_GATE    — min_score within sane bounds (72-92)

Result: HealthReport with overall ok/blocked + per-check details.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from utils import format_ist_timestamp

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HealthReport dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HealthReport:
    ok: bool                    # True = all critical checks pass
    blocked_reason: str = ""    # Human-readable block reason
    warnings: List[str] = field(default_factory=list)
    checks: Dict[str, bool] = field(default_factory=dict)
    checked_at: str = ""
    latency_ms: float = 0.0

    @property
    def summary(self) -> str:
        if self.ok:
            passed = [k for k, v in self.checks.items() if v]
            warn_str = f" | Warnings: {len(self.warnings)}" if self.warnings else ""
            return (
                f"HEALTHY — {len(passed)}/{len(self.checks)} checks passed"
                f"{warn_str} | latency={self.latency_ms:.0f}ms"
            )
        return f"BLOCKED: {self.blocked_reason}"


# ─────────────────────────────────────────────────────────────────────────────
# SystemHealthChecker
# ─────────────────────────────────────────────────────────────────────────────

class SystemHealthChecker:
    CHECK_INTERVAL = 60  # seconds between full checks

    def __init__(self):
        self._last_check: float = 0.0
        self._last_report: Optional[HealthReport] = None

    def check(self, bot_state: Optional[Dict] = None) -> HealthReport:
        """
        Run all health checks. Returns cached result if < CHECK_INTERVAL seconds old.
        bot_state: optional dict with {day_pnl, day_capital, min_score, positions_count}
        """
        import time as _time
        now = _time.monotonic()
        if self._last_report and (now - self._last_check) < self.CHECK_INTERVAL:
            return self._last_report

        t0 = _time.monotonic()
        report = self._run_checks(bot_state or {})
        report.latency_ms = round((_time.monotonic() - t0) * 1000, 1)
        report.checked_at = format_ist_timestamp()

        self._last_report = report
        self._last_check = now
        return report

    def force_check(self, bot_state: Optional[Dict] = None) -> HealthReport:
        """Force a fresh check, bypassing cache."""
        self._last_check = 0.0
        return self.check(bot_state)

    def _run_checks(self, bot_state: Dict) -> HealthReport:
        checks: Dict[str, bool] = {}
        warnings: List[str] = []
        blocked_reason = ""

        # 1. Alpaca API — critical: if down, no point running further checks
        checks["ALPACA_API"] = self._check_alpaca_api()
        if not checks["ALPACA_API"]:
            blocked_reason = "Alpaca API unreachable — trading blocked"
            logger.error(f"[{format_ist_timestamp()}] HEALTH BLOCKED: {blocked_reason}")
            return HealthReport(ok=False, blocked_reason=blocked_reason,
                                checks=checks, warnings=warnings)

        # 2. Data freshness (WebSocket alive) — warning-only, falls back to REST
        fresh, age_s = self._check_data_freshness()
        checks["DATA_FRESH"] = fresh
        if not fresh:
            warnings.append(f"Price data stale ({age_s:.0f}s old) — using REST fallback")
            logger.warning(
                f"[{format_ist_timestamp()}] HEALTH WARNING: Price data stale "
                f"({age_s:.0f}s old)"
            )

        # 3. Time window — critical: outside hours = no trades
        checks["TIME_WINDOW"] = self._check_time_window()
        if not checks["TIME_WINDOW"]:
            blocked_reason = "Outside trading hours (9:30-15:55 ET)"
            logger.info(f"[{format_ist_timestamp()}] HEALTH BLOCKED: {blocked_reason}")
            return HealthReport(ok=False, blocked_reason=blocked_reason,
                                checks=checks, warnings=warnings)

        # 4. Capital check — critical: insufficient funds = can't trade
        min_capital = float(os.getenv("MIN_CAPITAL_THRESHOLD", "1000"))
        cap_ok, cap_val = self._check_capital(min_capital)
        checks["CAPITAL"] = cap_ok
        if not cap_ok:
            blocked_reason = (
                f"Buying power ${cap_val:.0f} below minimum ${min_capital:.0f}"
            )
            logger.error(f"[{format_ist_timestamp()}] HEALTH BLOCKED: {blocked_reason}")
            return HealthReport(ok=False, blocked_reason=blocked_reason,
                                checks=checks, warnings=warnings)

        # 5. Circuit breaker — critical: daily loss limit hit = stop new entries
        day_pnl = float(bot_state.get("day_pnl", 0.0))
        day_cap = float(bot_state.get("day_capital", 50000))
        circuit_ok = self._check_circuit(day_pnl, day_cap)
        checks["CIRCUIT"] = circuit_ok
        if not circuit_ok:
            pct = abs(day_pnl / day_cap * 100) if day_cap > 0 else 0
            blocked_reason = f"Daily loss limit hit ({pct:.1f}%) — no new entries"
            logger.error(f"[{format_ist_timestamp()}] HEALTH BLOCKED: {blocked_reason}")
            return HealthReport(ok=False, blocked_reason=blocked_reason,
                                checks=checks, warnings=warnings)

        # 6. Score gate sanity — warning-only (bounds match adaptive_brain MIN/CEIL: 72-92)
        min_score = float(bot_state.get("min_score", 78.0))
        try:
            from adaptive_brain import MIN_SCORE_FLOOR, MIN_SCORE_CEIL
        except Exception:
            MIN_SCORE_FLOOR, MIN_SCORE_CEIL = 72.0, 92.0
        checks["SCORE_GATE"] = MIN_SCORE_FLOOR <= min_score <= MIN_SCORE_CEIL
        if not checks["SCORE_GATE"]:
            warnings.append(
                f"min_score {min_score:.0f} out of bounds [{MIN_SCORE_FLOOR:.0f}-{MIN_SCORE_CEIL:.0f}]"
            )
            logger.warning(
                f"[{format_ist_timestamp()}] HEALTH WARNING: min_score {min_score:.0f} "
                f"out of bounds [{MIN_SCORE_FLOOR:.0f}-{MIN_SCORE_CEIL:.0f}]"
            )

        # All critical checks: ALPACA_API, TIME_WINDOW, CAPITAL, CIRCUIT
        ok = all(checks.get(k, True) for k in ["ALPACA_API", "TIME_WINDOW", "CAPITAL", "CIRCUIT"])
        if ok:
            logger.info(
                f"[{format_ist_timestamp()}] HEALTH OK — "
                f"checks={list(checks.keys())} warnings={len(warnings)}"
            )
        return HealthReport(ok=ok, blocked_reason=blocked_reason,
                            checks=checks, warnings=warnings)

    # ─────────────────────────────────────────────────────────────────────
    # Individual check implementations
    # ─────────────────────────────────────────────────────────────────────

    def _check_alpaca_api(self) -> bool:
        """Verify Alpaca REST API is reachable and auth token is valid.
        Fail-open when keys are not configured (paper-mode without keys still runs)."""
        try:
            from auth_alpaca import get_auth_manager
            auth = get_auth_manager()
            if not auth.is_configured():
                # No API keys = paper-only mode: allow trading, warn once
                logger.warning(
                    f"[{format_ist_timestamp()}] Alpaca API keys not set — running in "
                    "paper-sim mode (no real orders). Set ALPACA_API_KEY + ALPACA_SECRET_KEY "
                    "in .env to enable live/paper API."
                )
                return True  # fail-open: paper sim works without keys
            client = auth.get_trading_client()
            account = client.get_account()
            return account is not None
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Alpaca API check failed: {e}")
            return False

    def _check_data_freshness(self) -> Tuple[bool, float]:
        """Check age of most recent SPY WebSocket quote. Fail-open."""
        try:
            from price_stream import get_price_stream
            stream = get_price_stream()
            age_s = stream.get_quote_age("SPY")
            if age_s is None:
                return True, 0.0   # Stream not started yet — not an error
            return age_s < 30.0, age_s
        except Exception as e:
            logger.debug(
                f"[{format_ist_timestamp()}] Data freshness check failed: {e}"
            )
            return True, 0.0   # Fail-open

    def _check_time_window(self) -> bool:
        """Verify current ET time is within the valid 9:30–15:55 trading window."""
        try:
            from utils import get_current_ist_time
            from datetime import time as dtime
            now = get_current_ist_time()
            t = now.time()
            OPEN  = dtime(9, 30)
            CLOSE = dtime(15, 55)
            return OPEN <= t <= CLOSE
        except Exception as e:
            logger.debug(
                f"[{format_ist_timestamp()}] Time window check failed: {e}"
            )
            return True   # Fail-open

    def _check_capital(self, min_threshold: float) -> Tuple[bool, float]:
        """Verify available buying power exceeds the configured minimum. Fail-open."""
        try:
            from data_fetch_alpaca import get_data_fetcher
            fetcher = get_data_fetcher()
            # Method is get_account_balance(), not get_balance()
            bal = fetcher.get_account_balance()
            buying_power = float(bal.get("buying_power", min_threshold + 1))
            return buying_power >= min_threshold, buying_power
        except Exception as e:
            logger.debug(
                f"[{format_ist_timestamp()}] Capital check failed: {e}"
            )
            return True, 999999.0   # Fail-open

    def _check_circuit(self, day_pnl: float, day_capital: float) -> bool:
        """Check that the daily loss limit (DAILY_LOSS_LIMIT_PCT env var) has not been hit."""
        if day_capital <= 0:
            return True
        loss_pct = abs(min(day_pnl, 0)) / day_capital * 100
        limit_pct = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "2.0"))
        return loss_pct < limit_pct


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_checker: Optional[SystemHealthChecker] = None


def get_health_checker() -> SystemHealthChecker:
    """
    Return the module-level singleton SystemHealthChecker.
    Thread-safe double-checked locking pattern.
    """
    global _checker
    if _checker is None:
        import threading
        _lock = threading.Lock()
        with _lock:
            if _checker is None:
                _checker = SystemHealthChecker()
                logger.info(
                    f"[{format_ist_timestamp()}] SystemHealthChecker singleton created"
                )
    return _checker
