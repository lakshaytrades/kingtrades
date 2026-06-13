"""
risk_manager.py — Dynamic Position Sizing for NSE India Intraday Bot

Implements three complementary sizing layers that compound on top of
half-Kelly to target Sharpe >= 2 and 15-20% monthly returns:

Layer 1 — Dynamic Kelly with Sharpe scaling
    Full Kelly x 0.5 (half-Kelly safety) then scaled by rolling Sharpe,
    signal score conviction, and ATR-based volatility.

Layer 2 — Omega ratio sizing multiplier
    Omega = sum(gains) / sum(losses).  Used as an additional size scaler
    on top of the Kelly fraction.

Layer 3 — Anti-martingale streak adjustment
    Press winners (x1.25 after 2 consecutive wins), cut losers
    (x0.6 after 2 consecutive losses), reset after 5 trades.

All functions are fail-open: errors return the safe conservative defaults
rather than raising exceptions, protecting the live trading session.

Usage:
    from india.risk_manager import dynamic_kelly_size, omega_ratio, \
                                    update_streak, get_streak_multiplier
"""
import logging
import math
from typing import List

logger = logging.getLogger("risk_manager")

# ── Module-level streak state ─────────────────────────────────────────────────

_streak_wins: int = 0       # current consecutive wins
_streak_losses: int = 0     # current consecutive losses
_streak_trades: int = 0     # trades since last reset


def reset_streak() -> None:
    """Reset streak counters.  Call once at market open each day."""
    global _streak_wins, _streak_losses, _streak_trades
    _streak_wins = _streak_losses = _streak_trades = 0
    logger.debug("risk_manager: streak state reset")


# ── Rolling Sharpe helper ─────────────────────────────────────────────────────

def rolling_sharpe(pnl_series: List[float], annual_factor: float = 252.0) -> float:
    """
    Annualised Sharpe ratio from a list of per-trade P&L fractions.

    Args:
        pnl_series: list of P&L as fraction of capital (e.g. [0.005, -0.003, ...])
        annual_factor: annualisation factor (252 trading days default)

    Returns:
        Sharpe ratio (0.0 on insufficient data or error)
    """
    try:
        n = len(pnl_series)
        if n < 5:
            return 1.0   # neutral default — not enough data
        mean = sum(pnl_series) / n
        variance = sum((x - mean) ** 2 for x in pnl_series) / max(n - 1, 1)
        std = math.sqrt(variance)
        if std < 1e-10:
            return 3.0   # perfect consistency -> high Sharpe
        return (mean / std) * (annual_factor ** 0.5)
    except Exception as e:
        logger.debug("risk_manager.rolling_sharpe: %s", e)
        return 1.0


# ── Omega ratio ───────────────────────────────────────────────────────────────

def omega_ratio(pnl_series: List[float], threshold: float = 0.0) -> float:
    """
    Omega ratio from a P&L series.

    Omega = sum(max(r - threshold, 0)) / sum(max(threshold - r, 0))

    Args:
        pnl_series: list of per-trade returns (fraction of capital)
        threshold:  minimum acceptable return (default = 0, i.e. break-even)

    Returns:
        Omega ratio (>= 0).  Returns 1.0 (neutral) on insufficient data.
        Returns 5.0 if there are gains but no losses (perfect).
    """
    try:
        if len(pnl_series) < 5:
            return 1.0
        gains  = sum(max(r - threshold, 0.0) for r in pnl_series)
        losses = sum(max(threshold - r, 0.0) for r in pnl_series)
        if losses < 1e-12:
            return 5.0 if gains > 0 else 1.0
        return max(0.0, gains / losses)
    except Exception as e:
        logger.debug("risk_manager.omega_ratio: %s", e)
        return 1.0


def _omega_multiplier(pnl_series: List[float]) -> float:
    """
    Convert Omega ratio into a position-sizing multiplier.

    Omega > 2  -> 1.2x  (strong edge)
    Omega 1-2  -> 1.0x  (neutral)
    Omega < 1  -> 0.7x  (edge degrading)
    """
    try:
        om = omega_ratio(pnl_series)
        if om > 2.0:
            return 1.2
        if om >= 1.0:
            return 1.0
        return 0.7
    except Exception:
        return 1.0


# ── Anti-martingale streak ────────────────────────────────────────────────────

def update_streak(pnl: float) -> None:
    """
    Update the module-level streak counters after each trade.

    Args:
        pnl: trade P&L in any currency — only sign matters.
    """
    global _streak_wins, _streak_losses, _streak_trades
    try:
        _streak_trades += 1
        if pnl > 0:
            _streak_wins  += 1
            _streak_losses = 0
        else:
            _streak_losses += 1
            _streak_wins   = 0
        # Reset after 5 trades regardless
        if _streak_trades >= 5:
            _streak_wins = _streak_losses = _streak_trades = 0
    except Exception as e:
        logger.debug("risk_manager.update_streak: %s", e)


def get_streak_multiplier() -> float:
    """
    Anti-martingale multiplier based on current streak.

    After 2+ consecutive wins   -> 1.25x  (press winners)
    After 2+ consecutive losses -> 0.60x  (cut exposure)
    Otherwise                   -> 1.00x  (neutral)

    Returns:
        float multiplier to apply to the base position size.
    """
    try:
        if _streak_wins >= 2:
            return 1.25
        if _streak_losses >= 2:
            return 0.60
        return 1.00
    except Exception:
        return 1.00


# ── Core Dynamic Kelly function ───────────────────────────────────────────────

def dynamic_kelly_size(
    recent_trades: List[float],
    capital: float,
    current_score: float,
    atr_pct: float,
    max_risk_pct: float = 0.02,
    max_pos_pct: float = 0.25,
) -> float:
    """
    Full Kelly x Sharpe-scaler x Score-scaler x Volatility-scaler x Omega.

    Computes the risk fraction of capital for a single trade.

    Args:
        recent_trades:  List of recent per-trade P&Ls as fraction of capital
                        (last 20 used).  e.g. [0.006, -0.003, 0.009, ...]
        capital:        Current account equity in INR.
        current_score:  Signal score 0-100 (higher = more conviction).
        atr_pct:        ATR as percentage of price (e.g. 0.025 = 2.5%).
        max_risk_pct:   Hard cap on risk per trade (default 2% = 0.02).
        max_pos_pct:    Hard cap on position value as fraction of capital
                        (default 25% = 0.25).  Caller enforces this separately.

    Returns:
        risk_fraction — fraction of capital to risk on this trade.
        Multiply by capital and divide by SL-distance to get quantity.

        Example:
            risk_frac = dynamic_kelly_size(trades, equity, score, atr_pct)
            qty = int(equity * risk_frac / sl_distance)

    Notes:
        - Fail-open: any exception returns the conservative cold-start default (0.005).
        - The returned value is already capped at max_risk_pct.
        - Does NOT include the anti-martingale streak — call get_streak_multiplier()
          separately and apply after this function if desired.
    """
    try:
        # ── Use last 20 trades ────────────────────────────────────────────────
        window = recent_trades[-20:] if len(recent_trades) > 20 else list(recent_trades)

        # ── Cold start: insufficient history ─────────────────────────────────
        if len(window) < 10:
            return 0.005   # 0.5% risk while warming up

        # ── Step 1: Full Kelly from win/loss stats ────────────────────────────
        wins   = [r for r in window if r > 0]
        losses = [r for r in window if r <= 0]

        n_wins   = len(wins)
        n_losses = len(losses)
        n_total  = n_wins + n_losses

        p = n_wins / n_total if n_total > 0 else 0.5   # empirical win rate
        q = 1.0 - p

        # b = avg_win / avg_loss (payoff ratio from recent trades)
        avg_win  = (sum(wins) / len(wins))    if wins   else 0.002
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.001
        b = avg_win / max(avg_loss, 1e-9)

        full_kelly = max(0.0, (p * b - q) / max(b, 1e-9))

        # ── Step 2: Half-Kelly safety ─────────────────────────────────────────
        half_kelly = full_kelly * 0.5

        # ── Step 3: Sharpe scaler ─────────────────────────────────────────────
        sharpe = rolling_sharpe(window)
        if sharpe > 2.0:
            sharpe_scale = 1.2
        elif sharpe >= 1.0:
            sharpe_scale = 1.0
        else:
            sharpe_scale = 0.6

        # ── Step 4: Score scaler (conviction) ────────────────────────────────
        score_scale = max(0.3, min(current_score / 100.0, 1.0))

        # ── Step 5: Volatility scaler (reduce size in high-vol stocks) ────────
        # atr_pct > 3% -> 0.5x; atr_pct 2-3% -> 0.75x; < 2% -> 1.0x
        if atr_pct > 0.03:
            vol_scale = 0.50
        elif atr_pct > 0.02:
            vol_scale = 0.75
        else:
            vol_scale = 1.00

        # ── Step 6: Omega ratio multiplier ────────────────────────────────────
        om_mult = _omega_multiplier(window)

        # ── Combine all scalers ───────────────────────────────────────────────
        raw = half_kelly * sharpe_scale * score_scale * vol_scale * om_mult

        # ── Hard caps ─────────────────────────────────────────────────────────
        # Risk per trade cap (default 2%)
        risk_capped = min(raw, max_risk_pct)
        risk_final  = max(0.001, risk_capped)   # at least 0.1% risk

        logger.debug(
            "dynamic_kelly: kelly=%.4f half=%.4f sharpe=%.2f(x%.2f) "
            "score=%.0f(x%.2f) atr=%.2f%%(x%.2f) omega(x%.2f) -> %.4f",
            full_kelly, half_kelly, sharpe, sharpe_scale,
            current_score, score_scale, atr_pct * 100, vol_scale, om_mult,
            risk_final,
        )
        return risk_final

    except Exception as e:
        logger.debug("risk_manager.dynamic_kelly_size: %s", e)
        return 0.005   # safe default on any error


# ── Convenience: full sizing with streak ──────────────────────────────────────

def compute_position_risk(
    recent_trades: List[float],
    capital: float,
    current_score: float,
    atr_pct: float,
    max_risk_pct: float = 0.02,
    max_pos_pct: float = 0.25,
) -> float:
    """
    Full pipeline: Dynamic Kelly x Anti-martingale streak multiplier.

    This is the primary entry-point for the live bot and backtester.

    Returns:
        risk_fraction (fraction of capital to risk on this trade).
    """
    try:
        base        = dynamic_kelly_size(
            recent_trades, capital, current_score, atr_pct,
            max_risk_pct, max_pos_pct,
        )
        streak_mult = get_streak_multiplier()
        combined    = base * streak_mult
        # Re-apply hard cap after streak multiplier
        return min(combined, max_risk_pct)
    except Exception as e:
        logger.debug("risk_manager.compute_position_risk: %s", e)
        return 0.005
