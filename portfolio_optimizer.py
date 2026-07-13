"""
portfolio_optimizer.py — Markowitz Minimum-Variance Portfolio Selection v31.0

When multiple signals fire simultaneously, select the subset that:
1. Maximizes total expected score (alpha)
2. Minimises portfolio correlation (diversification)

This is Markowitz (1952) "Portfolio Selection" adapted for signal selection.
Used by: Renaissance, Citadel, AQR — any fund that gets more signals than
they can trade.

Algorithm: Greedy Minimum Correlation Selection
  1. Sort candidates by signal score descending
  2. Take the top scorer unconditionally (highest alpha)
  3. For each remaining candidate: add only if avg correlation
     to already-selected portfolio < CORRELATION_THRESHOLD (0.65)
  4. Stop when MAX_POSITIONS reached

Correlation estimated from: SECTOR_MAP overlap (instant, no data needed)
Enhanced with: price return correlation from cached 5-min bars when available.

SECTOR_CORRELATION matrix (approximate pairwise correlations):
  Same sector:      0.80  (e.g. NVDA-AMD both semiconductor)
  Adjacent sector:  0.45  (e.g. TECH-SEMICONDUCTOR)
  Different sector: 0.20  (e.g. TECH-ENERGY)
  Unknown:          0.35  (default)
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

CORRELATION_THRESHOLD = 0.65   # max avg correlation to add a position
MAX_SELECTED = 8               # hard cap (matches config MAX_POSITIONS)

SECTOR_MAP: Dict[str, str] = {
    'NVDA':'SEMICONDUCTOR','AMD':'SEMICONDUCTOR','SOXL':'SEMICONDUCTOR',
    'SMCI':'SEMICONDUCTOR','AVGO':'SEMICONDUCTOR','MRVL':'SEMICONDUCTOR',
    'QCOM':'SEMICONDUCTOR','MU':'SEMICONDUCTOR','INTC':'SEMICONDUCTOR',
    'AAPL':'TECH','MSFT':'TECH','GOOG':'TECH','GOOGL':'TECH',
    'META':'TECH','CRM':'TECH','ORCL':'TECH','SNOW':'TECH',
    'PLTR':'TECH','DDOG':'TECH','NET':'TECH','ZS':'TECH',
    'GS':'FINANCE','JPM':'FINANCE','BAC':'FINANCE','MS':'FINANCE',
    'WFC':'FINANCE','C':'FINANCE','V':'FINANCE','MA':'FINANCE',
    'XOM':'ENERGY','CVX':'ENERGY','SLB':'ENERGY','OXY':'ENERGY',
    'COIN':'CRYPTO','MSTR':'CRYPTO','MARA':'CRYPTO','RIOT':'CRYPTO',
    'CLSK':'CRYPTO','HUT':'CRYPTO','BTBT':'CRYPTO','CIFR':'CRYPTO',
    'TSLA':'AUTO','RIVN':'AUTO','LCID':'AUTO','NIO':'AUTO',
    'SPY':'INDEX','QQQ':'INDEX','IWM':'INDEX','DIA':'INDEX',
    'MRNA':'BIOTECH','NVAX':'BIOTECH','BNTX':'BIOTECH',
}

ADJACENT_SECTORS = {
    'SEMICONDUCTOR': {'TECH'},
    'TECH': {'SEMICONDUCTOR', 'FINANCE'},
    'FINANCE': {'TECH'},
    'ENERGY': {'MATERIALS'},
    'CRYPTO': {'FINANCE'},
    'AUTO': {'TECH'},
    'BIOTECH': {'HEALTHCARE'},
}

def _sector_correlation(sym_a: str, sym_b: str) -> float:
    """Estimate pairwise correlation from sector membership."""
    sa = SECTOR_MAP.get(sym_a.upper(), 'OTHER')
    sb = SECTOR_MAP.get(sym_b.upper(), 'OTHER')
    if sa == sb:
        return 0.80
    if sb in ADJACENT_SECTORS.get(sa, set()) or sa in ADJACENT_SECTORS.get(sb, set()):
        return 0.45
    if sa == 'OTHER' or sb == 'OTHER':
        return 0.35
    return 0.20

def _return_correlation(sym_a: str, sym_b: str, bar_cache: Optional[Dict]) -> Optional[float]:
    """
    Estimate correlation from 5-min return series in bar_cache.
    Returns None if insufficient data.
    bar_cache: {symbol: DataFrame with 'close' column}
    """
    if bar_cache is None:
        return None
    try:
        df_a = bar_cache.get(sym_a)
        df_b = bar_cache.get(sym_b)
        if df_a is None or df_b is None:
            return None

        col_a = {c.lower(): c for c in df_a.columns}.get('close', 'Close')
        col_b = {c.lower(): c for c in df_b.columns}.get('close', 'Close')

        closes_a = df_a[col_a].values.astype(float)[-30:]
        closes_b = df_b[col_b].values.astype(float)[-30:]

        min_len = min(len(closes_a), len(closes_b))
        if min_len < 10:
            return None

        ret_a = np.diff(np.log(closes_a[-min_len:] + 1e-10))
        ret_b = np.diff(np.log(closes_b[-min_len:] + 1e-10))

        if np.std(ret_a) < 1e-10 or np.std(ret_b) < 1e-10:
            return None

        corr = float(np.corrcoef(ret_a, ret_b)[0, 1])
        return max(-1.0, min(1.0, corr))
    except Exception:
        return None

def get_correlation(sym_a: str, sym_b: str, bar_cache: Optional[Dict] = None) -> float:
    """
    Best-available correlation estimate.
    Uses return correlation if data available, else sector fallback.
    """
    ret_corr = _return_correlation(sym_a, sym_b, bar_cache)
    if ret_corr is not None:
        # Blend: 70% return-based, 30% sector-based (sector anchors vs noise)
        sector_corr = _sector_correlation(sym_a, sym_b)
        return 0.70 * ret_corr + 0.30 * sector_corr
    return _sector_correlation(sym_a, sym_b)

def select_optimal_portfolio(
    candidates: List[Tuple[str, float]],   # [(symbol, score), ...]
    bar_cache: Optional[Dict] = None,
    max_positions: int = MAX_SELECTED,
    correlation_threshold: float = CORRELATION_THRESHOLD,
) -> List[Tuple[str, float, str]]:
    """
    Greedy Minimum Correlation Selection.

    Args:
        candidates: list of (symbol, signal_score) tuples
        bar_cache: optional {symbol: df_5m} for return correlation
        max_positions: max number to select
        correlation_threshold: max allowed avg correlation to portfolio

    Returns:
        List of (symbol, score, reason) for selected positions.
        reason explains why each was included or excluded.

    Algorithm:
        1. Sort by score descending
        2. Always take #1 (highest score)
        3. For each remaining: compute avg correlation to selected set
           - if avg_corr < threshold: ADD (diversifying)
           - else: SKIP (too correlated, not diversifying enough)
        4. Stop at max_positions
    """
    if not candidates:
        return []

    # Sort descending by score
    sorted_candidates = sorted(candidates, key=lambda x: x[1], reverse=True)

    selected: List[str] = []
    result: List[Tuple[str, float, str]] = []
    rejected: List[Tuple[str, float, str]] = []

    for symbol, score in sorted_candidates:
        if len(selected) >= max_positions:
            break

        if not selected:
            # Always take the top scorer
            selected.append(symbol)
            result.append((symbol, score, f"PORTFOLIO[rank#1_score={score:.0f}]"))
            continue

        # Compute avg correlation to already-selected portfolio
        corrs = [get_correlation(symbol, sel, bar_cache) for sel in selected]
        avg_corr = float(np.mean(corrs))
        max_corr = float(np.max(corrs))

        # Reject if avg OR max correlation exceeds threshold — a single highly-correlated
        # existing position is enough to disqualify (avoids corr dilution via averaging)
        if avg_corr <= correlation_threshold and max_corr <= correlation_threshold:
            selected.append(symbol)
            result.append((
                symbol, score,
                f"PORTFOLIO[score={score:.0f}_avg_corr={avg_corr:.2f}_ADDED]"
            ))
        else:
            # Skip — too correlated with at least one existing position
            rejected.append((
                symbol, score,
                f"PORTFOLIO[score={score:.0f}_avg_corr={avg_corr:.2f}_max_corr={max_corr:.2f}_SKIPPED_correlated]"
            ))

    if rejected:
        logger.debug(
            f"[portfolio_optimizer] Selected {len(result)}/{len(candidates)} signals. "
            f"Rejected: {[f'{s}({sc:.0f})' for s,sc,_ in rejected]}"
        )

    return result


def get_portfolio_selection_summary(
    candidates: List[Tuple[str, float]],
    selected: List[Tuple[str, float, str]],
) -> str:
    """Human-readable summary for logging."""
    sel_syms = [s for s, _, _ in selected]
    rej_syms = [s for s, sc in candidates if s not in sel_syms]
    return (
        f"PORTFOLIO_OPT: {len(selected)}/{len(candidates)} selected "
        f"-> TAKE={sel_syms} | SKIP={rej_syms}"
    )
