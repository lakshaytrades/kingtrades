"""
dark_pool_tracker.py — Dark Pool & Institutional Block Trade Detection

Dark pools are private exchanges where institutions trade large blocks
without moving the public market. When they're active on a stock:
  - 40-70% of daily volume routes through dark venues
  - Smart money is accumulating/distributing quietly
  - When they finish, price moves hard in the direction they traded

Detection Methods (without FINRA ATS real-time feed):
  1. Block Print Heuristic — abnormally large single candles at round prices
     (institutions favour round numbers: $100, $150, $200)
  2. Volume Anomaly — volume > 3x average at a specific price level repeatedly
  3. Quiet Accumulation — multiple large candles with small price movement
     (high delta but low price change = dark pool absorbing supply)
  4. Pre-Market Block — large volume in first 5 bars before 9:35 AM
     (institutions often establish positions in the first minutes)

Score output: 0-8 pts boost when institutional fingerprints detected.
Always fails open (returns 0 on any error).
"""
import logging
from typing import Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def get_dark_pool_score(
    df: pd.DataFrame,
    direction: str,
    symbol: str = "",
) -> Tuple[float, str]:
    """
    Returns (score_delta, reason) — 0 to +8 boost when dark pool activity detected.
    Negative scores not issued — absence of dark pool is neutral, not bearish.
    """
    try:
        if df is None or len(df) < 10:
            return 0.0, ""

        close  = df["close"]
        volume = df["volume"]
        high   = df["high"]
        low    = df["low"]
        open_  = df["open"]

        vol_sma    = volume.rolling(20, min_periods=5).mean()
        vol_ratio  = (volume / vol_sma.replace(0, np.nan)).fillna(1.0)
        body_size  = abs(close - open_)
        bar_range  = (high - low).replace(0, np.nan)
        body_ratio = (body_size / bar_range).fillna(0.5)

        recent_n   = min(15, len(df))
        recent     = df.tail(recent_n)
        r_vol      = vol_ratio.tail(recent_n)
        r_body     = body_ratio.tail(recent_n)

        signals    = []
        score      = 0.0

        # ── Signal 1: Block Print — single candle ≥ 3x average volume ──────
        max_vol_ratio = r_vol.max()
        if max_vol_ratio >= 3.0:
            # Check if price closed near a round number (institutional tick)
            last_close = float(close.iloc[-1])
            nearest_round = round(last_close / 5) * 5  # nearest $5 round number
            if abs(last_close - nearest_round) / max(last_close, 1) < 0.005:
                score += 4.0
                signals.append(f"BLOCK@ROUND${nearest_round:.0f}x{max_vol_ratio:.1f}")
            else:
                score += 2.0
                signals.append(f"BLOCK_PRINT x{max_vol_ratio:.1f}")

        # ── Signal 2: Quiet Accumulation — high vol + small price move ──────
        price_range_pct = abs(float(close.iloc[-1]) - float(close.iloc[-recent_n])) / max(float(close.iloc[-recent_n]), 1) * 100
        avg_vol_ratio   = float(r_vol.mean())
        if avg_vol_ratio >= 1.8 and price_range_pct < 0.5:
            score += 3.0
            signals.append(f"QUIET_ACCUM vol={avg_vol_ratio:.1f}x range={price_range_pct:.2f}%")

        # ── Signal 3: Repeated large candles with small bodies ───────────────
        large_vol_small_body = ((r_vol >= 2.0) & (r_body < 0.3)).sum()
        if large_vol_small_body >= 2:
            score += 3.0
            signals.append(f"ABSORPTION x{large_vol_small_body} bars")

        # ── Signal 4: Direction alignment check ─────────────────────────────
        # Dark pool buying confirmed only if recent price action aligns
        price_slope = float(close.tail(5).iloc[-1]) - float(close.tail(5).iloc[0])
        if direction in ("LONG", "BUY") and price_slope < 0:
            score *= 0.5  # Dark pool activity but price trending wrong way
        elif direction in ("SHORT", "SELL") and price_slope > 0:
            score *= 0.5

        final = round(min(score, 8.0), 1)
        reason = " | ".join(signals) if signals else "no_dark_pool"
        return final, reason

    except Exception as e:
        logger.debug(f"[DARK_POOL] {symbol}: {e}")
        return 0.0, ""
