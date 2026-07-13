"""
ic_tracker.py — Adaptive Signal Information Coefficient Tracker (v27.0)

The single most important thing Renaissance does that retail algos don't:
they track which signals are working RIGHT NOW and weight them accordingly.

Information Coefficient (IC) = correlation between signal value and forward return.
IC = +1.0: perfect predictor. IC = 0: random noise. IC = -1.0: perfect inverse.

Renaissance re-weights signals weekly based on rolling 20-trade IC.
We do it per-symbol per-signal using a sliding window of trade outcomes.

Usage:
    record_signal_outcome(symbol, signal_name, signal_value, forward_return_pct)
    weight = get_signal_weight(signal_name, base_weight=1.0)

The weight multiplier ranges 0.3x (signal broken) to 1.8x (signal hot).
Default 1.0x when insufficient history.
"""
import logging
import json
import os
import math
from typing import Dict, List, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)

_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "data", "ic_history.json")
_MIN_SAMPLES = 10       # need at least 10 trades to compute IC
_WINDOW = 30            # rolling 30 trades
_MAX_WEIGHT = 1.8
_MIN_WEIGHT = 0.3

# In-memory store: {signal_name: [(signal_value, forward_return), ...]}
_history: Dict[str, List] = defaultdict(list)
_loaded = False

def _load():
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        os.makedirs(os.path.dirname(_HISTORY_FILE), exist_ok=True)
        if os.path.exists(_HISTORY_FILE):
            with open(_HISTORY_FILE) as f:
                data = json.load(f)
            for k, v in data.items():
                _history[k] = v[-_WINDOW:]
    except Exception as e:
        logger.debug(f"[ic_tracker] load failed: {e}")

def _save():
    try:
        os.makedirs(os.path.dirname(_HISTORY_FILE), exist_ok=True)
        with open(_HISTORY_FILE, "w") as f:
            json.dump({k: v[-_WINDOW:] for k, v in _history.items()}, f)
    except Exception as e:
        logger.debug(f"[ic_tracker] save failed: {e}")

def record_signal_outcome(signal_name: str, signal_value: float, forward_return_pct: float):
    """Call after trade closes. signal_value = score that was generated, forward_return = actual PnL%"""
    _load()
    _history[signal_name].append((float(signal_value), float(forward_return_pct)))
    if len(_history[signal_name]) > _WINDOW:
        _history[signal_name] = _history[signal_name][-_WINDOW:]
    if len(_history[signal_name]) % 5 == 0:  # save every 5 trades
        _save()

def _compute_ic(pairs: List) -> float:
    """Pearson correlation between signal values and forward returns."""
    if len(pairs) < _MIN_SAMPLES:
        return 0.0
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx  = math.sqrt(sum((x - mx)**2 for x in xs))
    dy  = math.sqrt(sum((y - my)**2 for y in ys))
    if dx < 1e-9 or dy < 1e-9:
        return 0.0
    return max(-1.0, min(1.0, num / (dx * dy)))

def get_signal_weight(signal_name: str, base_weight: float = 1.0) -> float:
    """
    Returns adaptive weight multiplier for this signal based on recent IC.
    IC > 0.15: signal is working well → up to 1.8x
    IC 0.05-0.15: signal has mild edge → 1.0-1.3x
    IC -0.05 to 0.05: noise → 0.7x
    IC < -0.05: signal is broken/inverted → 0.3x
    """
    _load()
    pairs = _history.get(signal_name, [])
    if len(pairs) < _MIN_SAMPLES:
        return base_weight  # no data = no change
    ic = _compute_ic(pairs)
    if ic > 0.20:
        mult = _MAX_WEIGHT
    elif ic > 0.15:
        mult = 1.5
    elif ic > 0.08:
        mult = 1.2
    elif ic > 0.02:
        mult = 1.0
    elif ic > -0.05:
        mult = 0.7
    else:
        mult = _MIN_WEIGHT
    logger.debug(f"[ic_tracker] {signal_name}: IC={ic:.3f} → {mult:.1f}x weight")
    return base_weight * mult

def get_ic_report() -> Dict:
    """Return IC for all tracked signals. Used for diagnostics/Telegram."""
    _load()
    report = {}
    for sig, pairs in _history.items():
        if len(pairs) >= _MIN_SAMPLES:
            report[sig] = {"ic": round(_compute_ic(pairs), 3), "n": len(pairs)}
    return report
