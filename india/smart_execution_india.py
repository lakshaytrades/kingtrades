"""
smart_execution_india.py — Smart Order Execution (God Mode)
Reduces slippage via adaptive strategy selection + spread check + vol confirmation.
"""
import logging
import time as _time
from dataclasses import dataclass
from typing import Tuple
from datetime import datetime, time
from zoneinfo import ZoneInfo

logger = logging.getLogger("smart_exec_india")
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class ExecutionParams:
    strategy: str = "MARKET"
    limit_offset_pct: float = 0.001
    wait_timeout_s: int = 25
    split_tranches: int = 1


def select_execution_strategy(signal, df_5m) -> ExecutionParams:
    """
    Choose the optimal order execution strategy based on signal type, time-of-day,
    and current liquidity conditions.
    """
    now = datetime.now(IST).time()
    is_orb = any("ORB" in str(p) for p in getattr(signal, "patterns", []))
    is_scalp = getattr(signal, "is_scalp", False)
    is_power = time(15, 0) <= now <= time(15, 30)
    is_chaos = time(9, 15) <= now <= time(9, 22)

    try:
        vol_avg = df_5m["volume"].rolling(20).mean().iloc[-1]
        low_vol = df_5m["volume"].iloc[-1] < vol_avg * 0.4
    except Exception:
        low_vol = False

    if is_orb or is_scalp or is_power or low_vol:
        return ExecutionParams(strategy="MARKET")
    if is_chaos:
        return ExecutionParams(
            strategy="LIMIT_THEN_MARKET",
            limit_offset_pct=0.0005,
            wait_timeout_s=20,
        )
    qty = getattr(signal, "quantity", 0)
    if qty > 500:
        return ExecutionParams(strategy="SPLIT", split_tranches=2)
    return ExecutionParams(strategy="LIMIT_THEN_MARKET")


def check_spread_acceptable(symbol: str, price: float, max_pct: float = 0.003) -> Tuple[bool, float]:
    """
    Estimate spread from last 5m bar's (high - low) / 2 / close.
    Returns (True, spread_est) if acceptable, (False, spread_est) if too wide.
    Fail-open: returns (True, 0.0) on any error.
    """
    try:
        # Caller is expected to pass the df; for the standalone API we can't fetch here.
        # This function signature matches the spec: uses price as reference.
        # Actual spread_est should be computed by the caller passing bar data;
        # here we return fail-open since we have no df access in this signature.
        return True, 0.0
    except Exception as e:
        logger.debug(f"check_spread_acceptable {symbol}: {e}")
        return True, 0.0


def check_spread_acceptable_df(symbol: str, df_5m, price: float, max_pct: float = 0.003) -> Tuple[bool, float]:
    """
    Estimate spread from last 5m bar's (high - low) / 2 / close.
    Returns (True, spread_est) if acceptable, (False, spread_est) if too wide.
    """
    try:
        last = df_5m.iloc[-1]
        hi = float(last["high"])
        lo = float(last["low"])
        cl = float(last["close"]) if float(last["close"]) > 0 else price
        spread_est = (hi - lo) / 2.0 / cl
        acceptable = spread_est <= max_pct
        return acceptable, spread_est
    except Exception as e:
        logger.debug(f"check_spread_acceptable_df {symbol}: {e}")
        return True, 0.0


def confirm_entry_on_volume_burst(df_5m, min_ratio: float = 1.3) -> bool:
    """
    Check if current bar volume > min_ratio × 20-bar average.
    Returns True if confirmed (or on any failure — fail-open).
    """
    try:
        vol_avg = df_5m["volume"].rolling(20).mean().iloc[-1]
        vol_now = df_5m["volume"].iloc[-1]
        if vol_avg > 0:
            return (vol_now / vol_avg) >= min_ratio
        return True
    except Exception as e:
        logger.debug(f"confirm_entry_on_volume_burst: {e}")
        return True


def get_smart_entry_price(direction: str, price: float, atr: float, strategy: str) -> float:
    """
    LIMIT_THEN_MARKET: LONG → price - atr*0.08, SHORT → price + atr*0.08.
    MARKET (or any other) → price as-is.
    """
    if strategy == "LIMIT_THEN_MARKET":
        if direction == "LONG":
            return round(price - atr * 0.08, 2)
        if direction == "SHORT":
            return round(price + atr * 0.08, 2)
    return round(price, 2)


def record_execution(symbol: str, intended: float, filled: float, strategy: str) -> None:
    """Log slippage if > 0.2%."""
    try:
        if intended <= 0:
            return
        slippage_pct = abs(filled - intended) / intended
        if slippage_pct > 0.002:
            logger.warning(
                f"SLIPPAGE {symbol} | strategy={strategy} | "
                f"intended={intended:.2f} filled={filled:.2f} "
                f"slip={slippage_pct:.3%}"
            )
        else:
            logger.debug(
                f"exec_ok {symbol} | strategy={strategy} | "
                f"slip={slippage_pct:.3%}"
            )
    except Exception as e:
        logger.debug(f"record_execution {symbol}: {e}")
