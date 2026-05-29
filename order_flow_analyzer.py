"""
order_flow_analyzer.py — Order Flow Imbalance (OFI) & Cumulative Delta

The #1 alpha edge in short-term trading: order flow tells you WHO is winning
the battle between buyers and sellers RIGHT NOW, before price confirms.

Methods:
  1. Cumulative Delta — rolling sum of (buy vol - sell vol) approximated from OHLCV
     Formula: up-candle vol = buy; down-candle vol = sell; doji = 50/50
  2. OFI Score — (buys - sells) / total over last N bars → 0.0-1.0 imbalance
  3. Delta Divergence — price making new high but delta not confirming = exhaustion
  4. Absorption — price steady but delta surging = quiet accumulation

Score output:
  +10 max: strong directional flow confirmation
  -10 max: opposing flow = fade the signal
   0: neutral / no data

Fail-open: returns 0 on any error — NEVER blocks a trade.
"""
import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

LOOKBACK_BARS = 10   # number of bars for OFI calculation
MIN_BARS      = 5    # minimum bars required to compute


def _approximate_delta(df: pd.DataFrame) -> pd.Series:
    """
    Approximate per-bar delta (buy - sell pressure) from OHLCV.
    Up bar: all volume is buying; Down bar: all volume is selling.
    Inside bar (close ≈ open): split proportionally by body position.
    """
    close = df["close"]
    open_ = df["open"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    bar_range = (high - low).replace(0, np.nan)
    # Proportion of bar that is bullish (close position within range)
    bull_pct = ((close - low) / bar_range).clip(0.0, 1.0).fillna(0.5)
    buy_vol  = vol * bull_pct
    sell_vol = vol * (1.0 - bull_pct)
    return (buy_vol - sell_vol)


def get_ofi_score(df: pd.DataFrame, direction: str, lookback: int = LOOKBACK_BARS) -> Tuple[float, str]:
    """
    Compute OFI (Order Flow Imbalance) score adjustment for a signal.

    Returns (score_delta, reason):
      score_delta: float in range [-10, +10]
      reason: human-readable explanation
    """
    try:
        if df is None or len(df) < MIN_BARS:
            return 0.0, "ofi:insufficient_bars"

        recent = df.tail(lookback).copy()
        delta  = _approximate_delta(recent)

        total_vol   = recent["volume"].sum()
        if total_vol <= 0:
            return 0.0, "ofi:no_volume"

        cum_delta   = delta.sum()
        buy_vol     = ((delta + recent["volume"]) / 2).sum()
        sell_vol    = ((recent["volume"] - delta) / 2).sum()
        ofi_ratio   = (buy_vol - sell_vol) / max(total_vol, 1)  # -1.0 to +1.0

        # Delta divergence: price up but cumulative delta declining = exhaustion
        price_trend  = recent["close"].iloc[-1] - recent["close"].iloc[0]
        delta_trend  = delta.iloc[-3:].mean() - delta.iloc[:3].mean()
        divergence   = (price_trend > 0 and delta_trend < 0) or (price_trend < 0 and delta_trend > 0)

        # Absorption signal: large delta but small price move = institutional accumulation
        price_move_pct = abs(price_trend) / max(recent["close"].iloc[0], 1) * 100
        absorption = abs(ofi_ratio) > 0.5 and price_move_pct < 0.15

        if direction in ("LONG", "BUY"):
            if ofi_ratio >= 0.6:
                base_delta = 10.0
            elif ofi_ratio >= 0.4:
                base_delta = 6.0
            elif ofi_ratio >= 0.2:
                base_delta = 3.0
            elif ofi_ratio <= -0.4:
                base_delta = -8.0
            elif ofi_ratio <= -0.2:
                base_delta = -4.0
            else:
                base_delta = 0.0
        else:  # SHORT / SELL
            if ofi_ratio <= -0.6:
                base_delta = 10.0
            elif ofi_ratio <= -0.4:
                base_delta = 6.0
            elif ofi_ratio <= -0.2:
                base_delta = 3.0
            elif ofi_ratio >= 0.4:
                base_delta = -8.0
            elif ofi_ratio >= 0.2:
                base_delta = -4.0
            else:
                base_delta = 0.0

        # Divergence penalty: price and flow disagree = signal unreliable
        if divergence:
            base_delta -= 5.0

        # Absorption bonus: quiet accumulation is very bullish
        if absorption:
            if (direction in ("LONG", "BUY") and ofi_ratio > 0) or \
               (direction in ("SHORT", "SELL") and ofi_ratio < 0):
                base_delta += 4.0

        final_delta = round(max(-10.0, min(10.0, base_delta)), 1)
        reason = (
            f"ofi={ofi_ratio:+.2f} cum_delta={cum_delta:+.0f} "
            f"{'DIVERGE' if divergence else ''} "
            f"{'ABSORB' if absorption else ''}"
        ).strip()
        return final_delta, reason

    except Exception as e:
        logger.debug(f"[OFI] error: {e}")
        return 0.0, "ofi:error"


def get_ofi_direction_ok(df: pd.DataFrame, direction: str, min_ofi: float = 0.15) -> Tuple[bool, str]:
    """
    Gate check: order flow must at minimum not strongly oppose the trade direction.
    Returns (ok, reason). Fails-open on error.
    """
    try:
        if df is None or len(df) < MIN_BARS:
            return True, ""
        recent   = df.tail(LOOKBACK_BARS).copy()
        delta    = _approximate_delta(recent)
        total    = recent["volume"].sum()
        if total <= 0:
            return True, ""
        buy_vol  = ((delta + recent["volume"]) / 2).sum()
        sell_vol = ((recent["volume"] - delta) / 2).sum()
        ofi      = (buy_vol - sell_vol) / total

        if direction in ("LONG", "BUY"):
            if ofi < -0.45:  # strong sell flow against a LONG = dangerous
                return False, f"OFI={ofi:+.2f} — heavy sell flow opposes LONG"
        else:
            if ofi > 0.45:   # strong buy flow against a SHORT = dangerous
                return False, f"OFI={ofi:+.2f} — heavy buy flow opposes SHORT"
        return True, f"ofi={ofi:+.2f}"
    except Exception:
        return True, ""  # fail open
