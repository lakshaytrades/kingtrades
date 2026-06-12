"""
microstructure_india.py — Intraday Microstructure & Tape Speed Engine

Renaissance edge: they read order flow at the microstructure level — not just
what price did but HOW FAST and with WHAT URGENCY.

On 5-min NSE bars we can reconstruct:
  1. Price Velocity     — how quickly price is moving (acceleration matters)
  2. Volume Footprint   — volume distribution across recent bars (is it surging?)
  3. Bar Efficiency     — how much of each bar's range was "used" (directional quality)
  4. Tick Urgency       — close position within bar (0=bottom, 1=top)
  5. Volume Imbalance   — comparing last 3 bars vol to 10-bar avg
  6. Momentum Cascade   — 1m/5m/15m/1h all pointing same direction

Score impact: -15 to +15
"""
import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("microstructure")


def _bar_close_position(bar: pd.Series) -> float:
    """Normalized close position within bar range: 0=bottom, 1=top."""
    rng = float(bar["high"]) - float(bar["low"])
    if rng <= 0:
        return 0.5
    return (float(bar["close"]) - float(bar["low"])) / rng


def analyze_microstructure(df_5m: pd.DataFrame,
                            df_1m: Optional[pd.DataFrame] = None) -> dict:
    """
    Analyze microstructure quality of a potential entry.
    Returns:
      {
        "score_adj": int,
        "velocity": float,        # price velocity (% per bar)
        "urgency": float,         # avg close position last 3 bars
        "vol_surge_ratio": float, # last bar vol / 10-bar avg
        "efficiency": float,      # avg bar efficiency (directional %age)
        "cascade": bool,          # 1m+5m aligned
        "signal": str,
      }
    """
    _empty = {"score_adj": 0, "velocity": 0.0, "urgency": 0.5,
              "vol_surge_ratio": 1.0, "efficiency": 0.5, "cascade": False,
              "signal": "NEUTRAL"}
    try:
        if df_5m is None or len(df_5m) < 15:
            return _empty

        df = df_5m.tail(15).copy().reset_index(drop=True)
        n = len(df)

        close  = df["close"].values.astype(float)
        high   = df["high"].values.astype(float)
        low    = df["low"].values.astype(float)
        volume = df["volume"].values.astype(float)
        open_  = df["open"].values.astype(float)

        avg_vol = np.mean(volume[:-1]) if len(volume) > 1 else volume[-1]

        # ── 1. Price Velocity ─────────────────────────────────────────────────
        # Rate of change of close over last 3 bars vs prior 3 bars
        if n >= 6:
            recent_move  = (close[-1] - close[-4]) / max(close[-4], 0.01) * 100
            prior_move   = (close[-4] - close[-7]) / max(close[-7], 0.01) * 100 if n >= 7 else 0.0
            velocity = recent_move
            acceleration = recent_move - prior_move
        else:
            velocity = (close[-1] - close[-2]) / max(close[-2], 0.01) * 100 if n >= 2 else 0.0
            acceleration = 0.0

        # ── 2. Tick Urgency (close position in last 3 bars) ───────────────────
        urgency_scores = []
        for i in range(max(0, n - 3), n):
            rng = high[i] - low[i]
            if rng > 0:
                urgency_scores.append((close[i] - low[i]) / rng)
        urgency = np.mean(urgency_scores) if urgency_scores else 0.5

        # ── 3. Volume Surge ───────────────────────────────────────────────────
        last_bar_vol  = volume[-1]
        vol_surge     = last_bar_vol / avg_vol if avg_vol > 0 else 1.0

        # Last 3 bars volume trend
        vol_trend_up = (len(volume) >= 3 and
                        volume[-1] > volume[-2] > volume[-3])

        # ── 4. Bar Efficiency ─────────────────────────────────────────────────
        # Directional efficiency: (abs(close-open)) / (high-low)
        efficiencies = []
        for i in range(max(0, n - 5), n):
            rng = high[i] - low[i]
            if rng > 0:
                efficiencies.append(abs(close[i] - open_[i]) / rng)
        efficiency = np.mean(efficiencies) if efficiencies else 0.5

        # ── 5. Direction from last bar ────────────────────────────────────────
        last_bar_up = close[-1] > open_[-1]

        # ── Score computation ─────────────────────────────────────────────────
        score_adj = 0
        signals = []

        # Velocity scoring
        if velocity > 0.5:   # strong upward move
            score_adj += min(6, int(velocity * 3))
            signals.append(f"VEL+{velocity:.1f}%")
        elif velocity < -0.5:
            score_adj -= min(6, int(abs(velocity) * 3))
            signals.append(f"VEL{velocity:.1f}%")

        # Urgency (LONG aligned: urgency > 0.7 is bullish)
        if urgency > 0.72:
            score_adj += 4
            signals.append(f"URG{urgency:.2f}")
        elif urgency < 0.35:
            score_adj -= 4

        # Volume surge
        if vol_surge > 2.5 and last_bar_up:
            score_adj += 5
            signals.append(f"VSURGE{vol_surge:.1f}x")
        elif vol_surge > 1.5:
            score_adj += 2
        elif vol_surge < 0.4:  # volume drying up — enter with caution
            score_adj -= 3

        # Volume trend
        if vol_trend_up:
            score_adj += 2

        # Bar efficiency
        if efficiency > 0.65:
            score_adj += 3
            signals.append(f"EFF{efficiency:.2f}")
        elif efficiency < 0.30:
            score_adj -= 3

        # 1m cascade check
        cascade = False
        if df_1m is not None and len(df_1m) >= 5:
            try:
                df1 = df_1m.tail(5).reset_index(drop=True)
                c1 = df1["close"].values.astype(float)
                if len(c1) >= 3:
                    m1_up = c1[-1] > c1[-2] > c1[-3]
                    m1_dn = c1[-1] < c1[-2] < c1[-3]
                    cascade = m1_up or m1_dn
                    if cascade:
                        score_adj += 4
                        signals.append("1M_CASCADE")
            except Exception:
                pass

        score_adj = max(-15, min(15, score_adj))
        primary_signal = "STRONG_TAPE" if score_adj >= 8 else "WEAK_TAPE" if score_adj <= -8 else "NEUTRAL_TAPE"

        return {
            "score_adj": score_adj,
            "velocity": round(velocity, 3),
            "urgency": round(urgency, 3),
            "vol_surge_ratio": round(vol_surge, 2),
            "efficiency": round(efficiency, 3),
            "cascade": cascade,
            "signal": primary_signal,
            "details": " | ".join(signals) if signals else "NEUTRAL",
        }

    except Exception as e:
        logger.debug(f"analyze_microstructure: {e}")
        return _empty


def get_microstructure_score(df_5m: pd.DataFrame,
                              direction: str,
                              df_1m: Optional[pd.DataFrame] = None) -> Tuple[int, str]:
    """
    Return (score_adj, reason) for the given direction.
    LONG: positive when bullish tape, negative when bearish
    SHORT: inverted
    """
    try:
        ms = analyze_microstructure(df_5m, df_1m)
        adj = ms["score_adj"]

        if direction == "SHORT":
            adj = -adj

        if adj == 0:
            return (0, "")

        reason = f"MS_{ms['signal']}:{adj:+d}"
        return (adj, reason)

    except Exception as e:
        logger.debug(f"get_microstructure_score: {e}")
        return (0, "")
