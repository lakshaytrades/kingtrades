"""
renaissance_mode.py — Institutional Meta-Layer v29.0

Six components that transform raw signal scoring into proper portfolio
construction — what separates Renaissance Technologies / Citadel from
retail algos. Sits above all 33 existing strategy layers.

Component 1: IC Tracker (Grinold & Kahn "Active Portfolio Management")
  Fundamental Law: IR = IC × sqrt(breadth).
  Track per-strategy Information Coefficient via EWM decay.
  IC = correlation(signal_strength, forward_return) over last 50 trades.
  Weight = clamp(0.5, 1.5, 1.0 + IC * 5.0). Persisted to logs/ic_state.json.

Component 2: Bayesian Score Transformation
  Prior P(win) = 0.55. Each confirming signal → Bayesian likelihood update.
  Maps raw_score through sigmoid centered at 65, n_confirmations add boost.
  Prevents score inflation from many weak confirmations stacking.

Component 3: Regime-Conditional Strategy Trust
  Trending (H>0.60): momentum × 1.30, mean_reversion × 0.70
  Choppy (H<0.40): momentum × 0.70, mean_reversion × 1.20
  High-vol (ATR>3%): all × 0.75

Component 4: Signal Freshness Decay (alpha decay research)
  Momentum half-life: 90 min. Structural: 240 min. Micro: 60 min.
  decay = max(0.30, 0.5^(minutes_since_open / half_life))
  Uses ET 9:30 AM market open (US bot).

Component 5: Sector Concentration Gate
  3+ open positions in same sector → new trade size × 0.40
  2 positions in sector → size × 0.70

Component 6: Drawdown-Aware Equity Curve Sizing
  DD=0%: 1.00x | DD<1%: 0.85x | DD<2%: 0.65x | DD≥3%: 0.40x
"""

import json
import logging
import math
import os
import time as _time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

ET = timezone(timedelta(hours=-4))  # Eastern Daylight Time (US markets)
MARKET_OPEN_HOUR_ET = 9
MARKET_OPEN_MIN_ET  = 30

SECTOR_MAP: Dict[str, str] = {
    'NVDA': 'SEMICONDUCTOR', 'AMD': 'SEMICONDUCTOR', 'SOXL': 'SEMICONDUCTOR',
    'SMCI': 'SEMICONDUCTOR', 'AVGO': 'SEMICONDUCTOR', 'MRVL': 'SEMICONDUCTOR',
    'QCOM': 'SEMICONDUCTOR', 'MU':   'SEMICONDUCTOR', 'INTC': 'SEMICONDUCTOR',
    'AAPL': 'TECH', 'MSFT': 'TECH', 'GOOG': 'TECH', 'GOOGL': 'TECH',
    'META': 'TECH', 'CRM':  'TECH', 'ORCL': 'TECH', 'SNOW':  'TECH',
    'PLTR': 'TECH', 'DDOG': 'TECH', 'NET':  'TECH', 'ZS':    'TECH',
    'GS':   'FINANCE', 'JPM': 'FINANCE', 'BAC': 'FINANCE', 'MS':  'FINANCE',
    'WFC':  'FINANCE', 'C':   'FINANCE', 'V':   'FINANCE', 'MA':  'FINANCE',
    'XOM':  'ENERGY',  'CVX': 'ENERGY',  'SLB': 'ENERGY',  'OXY': 'ENERGY',
    'COIN': 'CRYPTO',  'MSTR':'CRYPTO',  'MARA':'CRYPTO',  'RIOT':'CRYPTO',
    'CLSK': 'CRYPTO',  'HUT': 'CRYPTO',  'BTBT':'CRYPTO',  'CIFR':'CRYPTO',
    'TSLA': 'AUTO',    'RIVN':'AUTO',    'LCID':'AUTO',    'NIO': 'AUTO',
    'MRNA': 'BIOTECH', 'NVAX':'BIOTECH', 'BNTX':'BIOTECH',
}


class ICTracker:
    STATE_FILE = "logs/ic_state.json"
    ALPHA      = 0.15   # EWM learning rate
    MAX_BUFFER = 50     # samples per strategy

    def __init__(self):
        self._ic:     Dict[str, float] = {}
        self._buffer: Dict[str, List]  = {}
        self._load()

    def record_outcome(self, strategy: str, signal: float, ret: float) -> None:
        buf = self._buffer.setdefault(strategy, [])
        buf.append((float(signal), float(ret)))
        if len(buf) > self.MAX_BUFFER:
            buf.pop(0)
        if len(buf) >= 5:
            sigs = np.array([x[0] for x in buf])
            rets = np.array([x[1] for x in buf])
            if np.std(sigs) > 1e-10 and np.std(rets) > 1e-10:
                new_ic = float(np.corrcoef(sigs, rets)[0, 1])
            else:
                # Signals and/or returns lack variation — infer IC from mean return sign.
                # Positive mean return → positive IC; negative → negative IC.
                # Scaled to ±0.10 so weight moves modestly (1.0 ± 0.5 range respected).
                mean_ret = float(np.mean(rets))
                if abs(mean_ret) > 1e-10:
                    new_ic = float(np.sign(mean_ret)) * min(abs(mean_ret) * 5.0, 0.10)
                else:
                    new_ic = None
            if new_ic is not None:
                old_ic = self._ic.get(strategy, 0.0)
                self._ic[strategy] = self.ALPHA * new_ic + (1 - self.ALPHA) * old_ic
        self._save()

    def get_weight(self, strategy: str) -> float:
        ic = self._ic.get(strategy, 0.0)
        return max(0.5, min(1.5, 1.0 + ic * 5.0))

    def _load(self) -> None:
        try:
            if os.path.exists(self.STATE_FILE):
                with open(self.STATE_FILE) as f:
                    data = json.load(f)
                self._ic     = {k: float(v) for k, v in data.get('ic', {}).items()}
                self._buffer = data.get('buffer', {})
        except Exception as e:
            logger.debug(f"[ic_tracker] load error: {e}")

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.STATE_FILE) or '.', exist_ok=True)
            with open(self.STATE_FILE, 'w') as f:
                json.dump({'ic': self._ic, 'buffer': self._buffer}, f)
        except Exception as e:
            logger.debug(f"[ic_tracker] save error: {e}")


def get_bayesian_win_prob(raw_score: float, n_confirmations: int) -> float:
    """Bayesian P(win) from raw score and number of confirming signals."""
    try:
        score = max(0.0, min(100.0, float(raw_score)))
        # Sigmoid centered at 65, scale 15: maps 50→0.50, 65→0.73, 80→0.87, 95→0.95
        p_base = 1.0 / (1.0 + math.exp(-(score - 65.0) / 15.0))
        # Each confirmation above 4: +0.015 Bayesian boost, max +0.12
        n_boost = max(0, int(n_confirmations) - 4) * 0.015
        n_boost = min(0.12, n_boost)
        return min(0.95, p_base + n_boost)
    except Exception:
        return 0.55


def get_regime_weight_map(hurst_h: float, atr_ratio: float) -> Dict[str, float]:
    """Returns weight multipliers per signal category based on regime."""
    try:
        # Base weights
        w = {'momentum': 1.0, 'mean_reversion': 1.0,
             'microstructure': 1.0, 'fundamental': 1.0}
        h = float(hurst_h)
        atr = float(atr_ratio)

        if h > 0.60:                          # trending
            w['momentum']       = 1.30
            w['mean_reversion'] = 0.70
            w['microstructure'] = 1.10
        elif h < 0.40:                        # choppy / mean-reverting
            w['momentum']       = 0.70
            w['mean_reversion'] = 1.20
            w['microstructure'] = 1.00

        if atr > 0.030:                       # high volatility: capital preservation
            for k in w:
                w[k] *= 0.75

        return w
    except Exception:
        return {'momentum': 1.0, 'mean_reversion': 1.0,
                'microstructure': 1.0, 'fundamental': 1.0}


HALF_LIVES = {
    'microstructure': 60,
    'momentum':       90,
    'structural':     240,
    'fundamental':    360,
}


def get_freshness_decay(entry_time: Optional[datetime], strategy_type: str = 'momentum') -> float:
    """Signal freshness decay. Returns [0.30, 1.00]. 1.0 if no entry_time."""
    if entry_time is None:
        return 1.0
    try:
        now_et = datetime.now(ET)
        # Clamp to market open
        market_open = now_et.replace(
            hour=MARKET_OPEN_HOUR_ET, minute=MARKET_OPEN_MIN_ET,
            second=0, microsecond=0
        )
        minutes_since_open = max(0.0, (now_et - market_open).total_seconds() / 60.0)
        half_life = HALF_LIVES.get(strategy_type, 90)
        decay = 0.5 ** (minutes_since_open / half_life)
        return max(0.30, min(1.0, decay))
    except Exception:
        return 1.0


def get_sector_size_adjustment(symbol: str, open_position_symbols: List[str]) -> Tuple[float, str]:
    """Sector concentration gate. Returns (multiplier, reason)."""
    try:
        my_sector = SECTOR_MAP.get(symbol.upper(), 'OTHER')
        if my_sector == 'OTHER':
            return 1.0, ""
        same_sector = [s for s in open_position_symbols
                       if SECTOR_MAP.get(s.upper(), 'OTHER') == my_sector]
        n = len(same_sector)
        if n >= 3:
            return 0.40, f"SECTOR_GATE[{my_sector}_{n}_positions_0.40x]"
        elif n == 2:
            return 0.70, f"SECTOR_GATE[{my_sector}_{n}_positions_0.70x]"
        elif n == 1:
            return 0.85, f"SECTOR_GATE[{my_sector}_{n}_position_0.85x]"
        return 1.0, ""
    except Exception as e:
        logger.debug(f"[sector_gate] {e}")
        return 1.0, ""


def get_drawdown_size_multiplier(current_equity: float, peak_equity: float) -> Tuple[float, str]:
    """Drawdown-aware sizing. Returns (multiplier, reason)."""
    try:
        if peak_equity <= 0 or current_equity <= 0:
            return 1.0, ""
        dd = (float(peak_equity) - float(current_equity)) / float(peak_equity)
        dd = max(0.0, dd)
        if dd < 0.005:
            return 1.0, ""
        elif dd < 0.010:
            return 0.85, f"DD_SIZING[DD={dd*100:.1f}%_0.85x]"
        elif dd < 0.020:
            return 0.65, f"DD_SIZING[DD={dd*100:.1f}%_0.65x]"
        elif dd < 0.030:
            return 0.50, f"DD_SIZING[DD={dd*100:.1f}%_0.50x]"
        else:
            return 0.40, f"DD_SIZING[DD={dd*100:.1f}%_SURVIVAL_MODE_0.40x]"
    except Exception as e:
        logger.debug(f"[dd_sizing] {e}")
        return 1.0, ""


_ic_tracker = ICTracker()


def record_strategy_outcome(strategy: str, signal: float, ret: float) -> None:
    """Call when a trade closes. Updates IC weights."""
    _ic_tracker.record_outcome(strategy, signal, ret)


def get_renaissance_adjustment(
    symbol: str,
    direction: str,
    raw_score: float,
    n_confirmations: int,
    hurst_h: float,
    atr_ratio: float,
    open_position_symbols: List[str],
    current_equity: float,
    peak_equity: float,
    entry_time: Optional[datetime] = None,
) -> Tuple[float, float, str]:
    """
    Master Renaissance-style meta-adjustment.
    Returns (adjusted_score: float, size_multiplier: float, reason: str).
    Fail-open: returns (raw_score, 1.0, '') on any error.
    """
    reasons: List[str] = []
    try:
        # 1. Bayesian win probability → adjusted score
        p_win  = get_bayesian_win_prob(raw_score, n_confirmations)
        # Map P(win) back to [0,100] score space, but centered honestly
        # Score = 50 + (p_win - 0.55) / 0.45 * 50  → 50 at base rate, 100 at P=1.0
        p_score = 50.0 + (p_win - 0.55) / 0.45 * 50.0
        p_score = max(0.0, min(100.0, p_score))

        # Blend with raw score (don't swing too hard from Bayesian alone)
        adjusted_score = 0.60 * raw_score + 0.40 * p_score
        adjusted_score = max(0.0, min(100.0, adjusted_score))
        if abs(adjusted_score - raw_score) > 1.0:
            reasons.append(f"BAYES[P={p_win:.2f}_score{raw_score:.0f}→{adjusted_score:.0f}]")

        # 2. Regime weight — apply to score
        regime_w = get_regime_weight_map(hurst_h, atr_ratio)
        regime_mult = regime_w.get('momentum', 1.0)  # default momentum category
        if regime_mult != 1.0:
            adjusted_score = max(0.0, min(100.0, adjusted_score * regime_mult))
            reasons.append(f"REGIME[H={hurst_h:.2f}_w={regime_mult:.2f}x]")

        # 3. Freshness decay — apply to score as a soft penalty
        decay = get_freshness_decay(entry_time, 'momentum')
        if decay < 0.95:
            # Interpolate: decay=1.0 → no change, decay=0.30 → pull score toward 50
            adjusted_score = 50.0 + (adjusted_score - 50.0) * decay
            adjusted_score = max(0.0, min(100.0, adjusted_score))
            reasons.append(f"DECAY[{decay:.2f}x_freshness]")

        # 4. Size adjustments
        size_mult = 1.0

        sector_mult, sector_reason = get_sector_size_adjustment(symbol, open_position_symbols)
        if sector_mult != 1.0:
            size_mult *= sector_mult
            reasons.append(sector_reason)

        dd_mult, dd_reason = get_drawdown_size_multiplier(current_equity, peak_equity)
        if dd_mult != 1.0:
            size_mult *= dd_mult
            reasons.append(dd_reason)

        size_mult = max(0.25, min(1.5, size_mult))
        combined_reason = " | ".join(r for r in reasons if r)
        return adjusted_score, size_mult, combined_reason

    except Exception as e:
        logger.debug(f"[suppressed] renaissance_adjustment: {e}")
        return float(raw_score), 1.0, ""
