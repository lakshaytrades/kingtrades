"""
portfolio_guard.py — Real-time portfolio risk management

Prevents correlated positions, sector concentration, and portfolio heat
overflow. Top-1% traders never hold correlated positions simultaneously.
Fail-open — never blocks on error.
"""

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# ── Correlation clusters: symbols in same cluster = highly correlated ─────────
CORRELATION_CLUSTERS: Dict[str, List[str]] = {
    "semiconductors": [
        "NVDA", "AMD", "INTC", "QCOM", "AVGO", "MU", "AMAT",
        "LRCX", "KLAC", "MRVL", "SMCI",
    ],
    "mega_tech": ["AAPL", "MSFT", "GOOGL", "META", "AMZN"],
    "ev_auto":   ["TSLA", "RIVN", "LCID", "F", "GM"],
    "banks":     ["JPM", "BAC", "GS", "MS", "WFC", "C"],
    "biotech":   ["MRNA", "BNTX", "BIIB", "GILD", "REGN", "VRTX"],
    "energy":    ["XOM", "CVX", "COP", "SLB", "EOG", "PXD"],
    "retail":    ["AMZN", "WMT", "TGT", "COST", "HD", "LOW"],
    "social_media": ["META", "SNAP", "PINS", "RDDT"],
    "cloud":     ["AMZN", "MSFT", "GOOGL", "CRM", "NOW", "SNOW", "DDOG"],
    "crypto_proxy": ["COIN", "MSTR", "RIOT", "MARA", "HUT"],
}


def _build_symbol_cluster_map() -> Dict[str, List[str]]:
    """Return {symbol: [cluster_name, ...]} — a symbol may be in multiple clusters."""
    mapping: Dict[str, List[str]] = {}
    for cluster, syms in CORRELATION_CLUSTERS.items():
        for sym in syms:
            mapping.setdefault(sym.upper(), []).append(cluster)
    return mapping


_SYMBOL_CLUSTER_MAP = _build_symbol_cluster_map()


def check_correlation_risk(
    new_symbol: str,
    open_positions: List[str],
) -> Tuple[bool, str, float]:
    """
    Check if *new_symbol* is highly correlated with any existing open position.

    Args:
        new_symbol:     Ticker being considered for a new trade.
        open_positions: List of ticker strings currently held.

    Returns:
        (is_risky, reason, risk_score)
        - is_risky   : True if at least 1 existing position shares a cluster.
        - reason     : Human-readable explanation.
        - risk_score : 0.0 = no correlation, 0.5 = 1 overlap, 1.0 = 2+ overlaps
                       (values match the caller contract).
    """
    try:
        new_sym = new_symbol.upper()
        new_clusters = set(_SYMBOL_CLUSTER_MAP.get(new_sym, []))

        if not new_clusters:
            return False, f"{new_symbol} not in any known cluster", 0.0

        if not open_positions:
            return False, "no open positions", 0.0

        # Count how many existing positions share a cluster with new_symbol
        cluster_hits: Dict[str, List[str]] = {}  # cluster -> list of clashing symbols
        for pos_sym in open_positions:
            pos_sym_upper = pos_sym.upper()
            pos_clusters  = set(_SYMBOL_CLUSTER_MAP.get(pos_sym_upper, []))
            shared = new_clusters & pos_clusters
            for c in shared:
                cluster_hits.setdefault(c, []).append(pos_sym_upper)

        if not cluster_hits:
            return False, f"{new_symbol} has no cluster overlap with open positions", 0.0

        # Total distinct open symbols that overlap
        all_clashing = sorted({s for syms in cluster_hits.values() for s in syms})
        max_in_cluster = max(len(v) for v in cluster_hits.values())

        if max_in_cluster >= 2:
            # 2+ existing positions already in same cluster — block
            worst_cluster = max(cluster_hits, key=lambda c: len(cluster_hits[c]))
            reason = (
                f"{new_symbol} would be the 3rd+ position in [{worst_cluster}] cluster "
                f"(already holding: {', '.join(cluster_hits[worst_cluster])})"
            )
            return True, reason, 1.0
        else:
            # 1 existing position in same cluster — warn, allow
            worst_cluster = next(iter(cluster_hits))
            reason = (
                f"{new_symbol} shares [{worst_cluster}] cluster with "
                f"{', '.join(all_clashing)} — elevated correlation risk"
            )
            return True, reason, 0.5

    except Exception as exc:
        logger.debug(f"[portfolio_guard] check_correlation_risk error: {exc}")
        return False, f"check suppressed: {exc}", 0.0


def check_sector_concentration(
    new_symbol: str,
    open_positions: List[str],
    max_per_sector: int = 3,
) -> Tuple[bool, str]:
    """
    Check if adding *new_symbol* would exceed *max_per_sector* positions in the
    same sector.  Uses SECTOR_MAP from watchlist_manager.py.

    Args:
        new_symbol:     Ticker being considered.
        open_positions: List of tickers currently held.
        max_per_sector: Maximum allowed positions in a single sector.

    Returns:
        (is_risky, reason)
    """
    try:
        from watchlist_manager import SECTOR_MAP

        # Build symbol -> sector map (first match wins)
        sym_sector: Dict[str, str] = {}
        for sector, syms in SECTOR_MAP.items():
            for sym in syms:
                sym_upper = sym.upper()
                if sym_upper not in sym_sector:
                    sym_sector[sym_upper] = sector

        new_sym    = new_symbol.upper()
        new_sector = sym_sector.get(new_sym, None)

        if new_sector is None:
            return False, f"{new_symbol} not mapped to any sector"

        # Count existing open positions in the same sector
        same_sector = [
            p for p in open_positions
            if sym_sector.get(p.upper()) == new_sector
        ]

        if len(same_sector) >= max_per_sector:
            reason = (
                f"{new_symbol} would exceed max {max_per_sector} positions in "
                f"sector [{new_sector}] (already: {', '.join(same_sector)})"
            )
            return True, reason

        return False, (
            f"{new_symbol} sector [{new_sector}] has {len(same_sector)}/{max_per_sector} "
            "positions — within limit"
        )

    except Exception as exc:
        logger.debug(f"[portfolio_guard] check_sector_concentration error: {exc}")
        return False, f"sector check suppressed: {exc}"


def check_portfolio_heat(
    open_positions: List[dict],
    daily_capital: float,
    new_risk_amount: float,
) -> Tuple[bool, str, float]:
    """
    Portfolio heat = sum of all open risk amounts / daily_capital.

    Args:
        open_positions:  List of dicts with keys: symbol, stop_loss, entry, qty
        daily_capital:   Total capital for the day ($).
        new_risk_amount: $ at risk for the proposed new trade.

    Returns:
        (allowed, reason, heat_pct)
        - allowed   : False if total heat would exceed 8%.
        - reason    : Explanation.
        - heat_pct  : Projected total heat % after adding new trade.
    """
    try:
        if daily_capital <= 0:
            return True, "daily_capital not set — heat check skipped", 0.0

        # Calculate current heat
        current_heat = 0.0
        for pos in open_positions:
            try:
                entry = float(pos.get("entry", 0) or 0)
                sl    = float(pos.get("stop_loss", 0) or 0)
                qty   = float(pos.get("qty", 0) or 0)
                if entry > 0 and sl > 0 and qty > 0:
                    current_heat += abs(entry - sl) * qty
            except (TypeError, ValueError):
                continue

        projected_heat     = current_heat + max(new_risk_amount, 0.0)
        projected_heat_pct = projected_heat / daily_capital * 100.0

        if projected_heat_pct > 8.0:
            reason = (
                f"Portfolio heat CRITICAL: projected {projected_heat_pct:.1f}% > 8% limit "
                f"(current={current_heat / daily_capital * 100:.1f}%, "
                f"new_risk=${new_risk_amount:.0f})"
            )
            return False, reason, round(projected_heat_pct, 2)

        if projected_heat_pct > 6.0:
            reason = (
                f"Portfolio heat HOT: {projected_heat_pct:.1f}% (>6%) — "
                "consider reducing position size"
            )
            return True, reason, round(projected_heat_pct, 2)

        reason = f"Portfolio heat OK: {projected_heat_pct:.1f}% of capital"
        return True, reason, round(projected_heat_pct, 2)

    except Exception as exc:
        logger.debug(f"[portfolio_guard] check_portfolio_heat error: {exc}")
        return True, f"heat check suppressed: {exc}", 0.0


def get_portfolio_snapshot(
    open_positions: List[dict],
    daily_capital: float,
) -> dict:
    """
    Returns real-time portfolio health.

    Args:
        open_positions: List of dicts with keys: symbol, stop_loss, entry, qty
        daily_capital:  Total capital for the day ($).

    Returns dict with:
        total_heat_pct        -- % of capital at risk right now
        sector_breakdown      -- {sector: count}
        correlation_warnings  -- clusters with > 1 position
        max_correlated_cluster-- cluster with most positions
        heat_status           -- 'COOL' <3%, 'WARM' 3-6%, 'HOT' 6-8%, 'CRITICAL' >8%
        recommended_action    -- 'ADD_MORE', 'HOLD', 'REDUCE', 'EMERGENCY_EXIT'
    """
    try:
        # ── Total heat ────────────────────────────────────────────────────────
        total_heat = 0.0
        symbols: List[str] = []
        for pos in open_positions:
            try:
                entry = float(pos.get("entry", 0) or 0)
                sl    = float(pos.get("stop_loss", 0) or 0)
                qty   = float(pos.get("qty", 0) or 0)
                sym   = str(pos.get("symbol", "")).upper()
                if entry > 0 and sl > 0 and qty > 0:
                    total_heat += abs(entry - sl) * qty
                if sym:
                    symbols.append(sym)
            except (TypeError, ValueError):
                continue

        heat_pct = (total_heat / daily_capital * 100.0) if daily_capital > 0 else 0.0

        # ── Heat status ───────────────────────────────────────────────────────
        if heat_pct > 8.0:
            heat_status        = "CRITICAL"
            recommended_action = "EMERGENCY_EXIT"
        elif heat_pct > 6.0:
            heat_status        = "HOT"
            recommended_action = "REDUCE"
        elif heat_pct > 3.0:
            heat_status        = "WARM"
            recommended_action = "HOLD"
        else:
            heat_status        = "COOL"
            recommended_action = "ADD_MORE"

        # ── Sector breakdown ──────────────────────────────────────────────────
        sector_breakdown: Dict[str, int] = {}
        try:
            from watchlist_manager import SECTOR_MAP
            sym_sector: Dict[str, str] = {}
            for sector, syms in SECTOR_MAP.items():
                for sym in syms:
                    sym_sector.setdefault(sym.upper(), sector)
            for sym in symbols:
                sec = sym_sector.get(sym, "OTHER")
                sector_breakdown[sec] = sector_breakdown.get(sec, 0) + 1
        except Exception:
            for sym in symbols:
                sector_breakdown.setdefault("UNKNOWN", 0)
                sector_breakdown["UNKNOWN"] += 1

        # ── Correlation warnings ──────────────────────────────────────────────
        cluster_counts: Dict[str, List[str]] = {}
        for sym in symbols:
            for cluster in _SYMBOL_CLUSTER_MAP.get(sym, []):
                cluster_counts.setdefault(cluster, []).append(sym)

        correlation_warnings = [
            f"{cluster}: {', '.join(syms)}"
            for cluster, syms in cluster_counts.items()
            if len(syms) > 1
        ]
        max_correlated_cluster = (
            max(cluster_counts, key=lambda c: len(cluster_counts[c]))
            if cluster_counts else ""
        )

        return {
            "total_heat_pct":         round(heat_pct, 2),
            "sector_breakdown":       sector_breakdown,
            "correlation_warnings":   correlation_warnings,
            "max_correlated_cluster": max_correlated_cluster,
            "heat_status":            heat_status,
            "recommended_action":     recommended_action,
        }

    except Exception as exc:
        logger.debug(f"[portfolio_guard] get_portfolio_snapshot error: {exc}")
        return {
            "total_heat_pct":         0.0,
            "sector_breakdown":       {},
            "correlation_warnings":   [],
            "max_correlated_cluster": "",
            "heat_status":            "COOL",
            "recommended_action":     "ADD_MORE",
        }
