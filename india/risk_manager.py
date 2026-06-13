"""
risk_manager.py — Dynamic Position Sizing for NSE India Intraday Bot

Implements complementary sizing layers that compound on top of
half-Kelly to target Sharpe >= 3 and 15-20% monthly returns:

Layer 1 — Dynamic Kelly with Sharpe/Sortino scaling
    Full Kelly x 0.5 (half-Kelly safety) then scaled by rolling Sharpe,
    Sortino ratio, signal score conviction, and ATR-based volatility.

Layer 2 — Omega ratio sizing multiplier
    Omega = sum(gains) / sum(losses).  Used as an additional size scaler
    on top of the Kelly fraction.

Layer 3 — Anti-martingale streak adjustment
    Press winners (x1.25 after 2 consecutive wins), cut losers
    (x0.6 after 2 consecutive losses), reset after 5 trades.

Layer 4 — CVaR-based size cap
    Conditional Value at Risk guards against fat-tail NSE event risk.

Layer 5 — Time-of-day multiplier
    Reduces size during afternoon lull and squareoff pressure window.

Layer 6 — Intraday circuit breaker
    Halts new entries at -3% daily drawdown, emergency exit at -5%.

Layer 7 — Portfolio heat check
    Prevents total open risk from exceeding 6% of capital.

All functions are fail-open: errors return the safe conservative defaults
rather than raising exceptions, protecting the live trading session.

Usage:
    from india.risk_manager import dynamic_kelly_size, omega_ratio, \
                                    update_streak, get_streak_multiplier, \
                                    sortino_ratio, cvar_size_cap, \
                                    time_of_day_multiplier, \
                                    set_daily_start_equity, check_intraday_circuit, \
                                    is_trading_halted, portfolio_heat, \
                                    can_add_position
"""
import logging
import math
from typing import List

logger = logging.getLogger("risk_manager")

# ── Module-level streak state ─────────────────────────────────────────────────

_streak_wins: int = 0       # current consecutive wins
_streak_losses: int = 0     # current consecutive losses
_streak_trades: int = 0     # trades since last reset

# ── Module-level intraday circuit breaker state ───────────────────────────────

_daily_start_equity: float = 0.0
_intraday_halt: bool = False
_intraday_emergency: bool = False


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
        Sharpe ratio (1.0 on insufficient data or error)
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


# ── Sortino Ratio ─────────────────────────────────────────────────────────────

def sortino_ratio(pnl_series: list, annual_factor: float = 252,
                  target_return: float = 0.0) -> float:
    """
    Sortino Ratio — like Sharpe but only penalizes downside deviation.
    Better metric for asymmetric momentum strategies.

    Returns annualized Sortino. Default 1.0 for < 5 trades.
    """
    if len(pnl_series) < 5:
        return 1.0
    try:
        import numpy as np
        arr = np.array(pnl_series, dtype=float)
        # Remove inf/nan
        arr = arr[np.isfinite(arr)]
        if len(arr) < 5:
            return 1.0
        mean_ret = float(np.mean(arr))
        # Downside deviation: only negative returns vs target
        downside = arr[arr < target_return] - target_return
        if len(downside) == 0:
            return 5.0  # No downside = excellent
        downside_std = float(np.sqrt(np.mean(downside ** 2)))
        if downside_std < 1e-9:
            return 5.0
        sortino = (mean_ret / downside_std) * (annual_factor ** 0.5)
        return float(np.clip(sortino, -5.0, 10.0))
    except Exception:
        return 1.0


# ── CVaR-based Size Cap ───────────────────────────────────────────────────────

def cvar_size_cap(pnl_series: list, confidence: float = 0.95,
                  base_max_risk: float = 0.02) -> float:
    """
    Conditional Value at Risk (CVaR/Expected Shortfall) based position size cap.

    If tail losses are extreme relative to average losses, reduce max risk.
    Protects against fat-tail risk (common in NSE during events/circuit breakers).

    Returns: max_risk_pct (between 0.003 and base_max_risk)
    """
    if len(pnl_series) < 10:
        return base_max_risk
    try:
        import numpy as np
        arr = np.array(pnl_series, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) < 10:
            return base_max_risk

        # Sort and find VaR threshold
        sorted_losses = np.sort(arr)  # ascending (most negative first)
        var_idx = int(len(sorted_losses) * (1 - confidence))
        var_threshold = sorted_losses[max(0, var_idx)]

        # CVaR = mean of losses beyond VaR
        tail_losses = sorted_losses[:max(1, var_idx + 1)]
        cvar = float(np.mean(tail_losses))  # negative number
        avg_loss = float(np.mean(arr[arr < 0])) if np.any(arr < 0) else -0.001

        # If CVaR is > 3x average loss: reduce size
        if avg_loss != 0 and cvar / avg_loss > 3.0:
            # Tail risk is very fat: reduce
            reduction = min(0.7, (cvar / avg_loss - 3.0) * 0.1)
            return max(0.003, base_max_risk * (1 - reduction))

        return base_max_risk
    except Exception:
        return base_max_risk


# ── Time-of-Day Risk Multiplier ───────────────────────────────────────────────

def time_of_day_multiplier(bar_ts=None) -> float:
    """
    NSE intraday risk multiplier by time of day.

    First hour: high momentum -> full size
    Afternoon: low momentum + squareoff pressure -> reduce size

    Pass bar_ts as pd.Timestamp or datetime. Returns float multiplier.
    """
    try:
        from datetime import time as dtime
        if bar_ts is None:
            return 1.0
        if hasattr(bar_ts, 'time'):
            t = bar_ts.time()
        elif hasattr(bar_ts, 'hour'):
            from datetime import time as dtime
            t = dtime(bar_ts.hour, bar_ts.minute)
        else:
            return 1.0

        if t < dtime(10, 30):
            return 1.1   # First 75 min: highest momentum
        elif t < dtime(12, 0):
            return 1.0   # Mid-morning: normal
        elif t < dtime(13, 0):
            return 0.85  # Pre-lunch: slightly reduced
        elif t < dtime(14, 30):
            return 0.7   # Afternoon: reduced (afternoon lull)
        else:
            return 0.5   # Last 50 min: squareoff pressure, very reduced
    except Exception:
        return 1.0


# ── Intraday Circuit Breaker ──────────────────────────────────────────────────

def set_daily_start_equity(equity: float):
    """Call at market open (9:15 IST) with starting equity for the day."""
    global _daily_start_equity, _intraday_halt, _intraday_emergency
    _daily_start_equity = equity
    _intraday_halt = False
    _intraday_emergency = False


def check_intraday_circuit(current_equity: float) -> str:
    """
    Intraday equity curve protection.

    Returns:
        'OK'        -- continue trading normally
        'HALT'      -- stop new entries (down 3%), let existing trades run
        'EMERGENCY' -- exit all positions immediately (down 5%)
    """
    global _intraday_halt, _intraday_emergency
    if _daily_start_equity <= 0:
        return 'OK'

    drawdown_pct = (_daily_start_equity - current_equity) / _daily_start_equity

    if drawdown_pct >= 0.05:
        _intraday_emergency = True
        _intraday_halt = True
        return 'EMERGENCY'
    elif drawdown_pct >= 0.03:
        _intraday_halt = True
        return 'HALT'
    else:
        return 'OK'


def is_trading_halted() -> bool:
    """Returns True if daily circuit breaker has tripped."""
    return _intraday_halt


# ── Portfolio Heat Check ──────────────────────────────────────────────────────

def portfolio_heat(open_trades_risks: list) -> float:
    """
    Total portfolio heat = sum of all open position risk fractions.

    Args:
        open_trades_risks: list of floats, each = (SL_distance x qty) / capital

    Returns: total heat as fraction of capital (e.g., 0.04 = 4% total heat)
    """
    try:
        return float(sum(open_trades_risks))
    except Exception:
        return 0.0


def can_add_position(open_trades_risks: list, new_risk: float,
                     max_heat: float = 0.06) -> bool:
    """
    Returns True if adding a new position would keep portfolio heat under max_heat (6% default).
    """
    current_heat = portfolio_heat(open_trades_risks)
    return (current_heat + new_risk) <= max_heat


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
    Full Kelly x Sharpe/Sortino-scaler x Score-scaler x Volatility-scaler x Omega x CVaR-cap.

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
        risk_fraction -- fraction of capital to risk on this trade.
        Multiply by capital and divide by SL-distance to get quantity.

        Example:
            risk_frac = dynamic_kelly_size(trades, equity, score, atr_pct)
            qty = int(equity * risk_frac / sl_distance)

    Notes:
        - Fail-open: any exception returns the conservative cold-start default (0.005).
        - The returned value is already capped at max_risk_pct and CVaR cap.
        - Does NOT include the anti-martingale streak -- call get_streak_multiplier()
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

        # ── Step 3: Sharpe + Sortino scaler ──────────────────────────────────
        sharpe  = rolling_sharpe(window)
        sortino = sortino_ratio(window)
        # Use the geometric mean for balanced assessment
        eff_ratio = (sharpe + sortino) / 2.0
        # Scaler based on effective ratio (replaces sharpe-only scaler)
        if eff_ratio >= 2.5:
            ratio_scaler = 1.3
        elif eff_ratio >= 2.0:
            ratio_scaler = 1.2
        elif eff_ratio >= 1.5:
            ratio_scaler = 1.1
        elif eff_ratio >= 1.0:
            ratio_scaler = 1.0
        elif eff_ratio >= 0.5:
            ratio_scaler = 0.8
        else:
            ratio_scaler = 0.6

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
        raw = half_kelly * ratio_scaler * score_scale * vol_scale * om_mult

        # ── Step 7: CVaR cap ──────────────────────────────────────────────────
        # After computing base_risk, apply CVaR cap
        max_risk_capped = cvar_size_cap(window, base_max_risk=max_risk_pct)
        # Final cap is min of normal cap and CVaR cap
        final_risk = min(raw, max_risk_capped)

        # ── Hard floor ────────────────────────────────────────────────────────
        risk_final = max(0.001, final_risk)   # at least 0.1% risk

        logger.debug(
            "dynamic_kelly: kelly=%.4f half=%.4f sharpe=%.2f sortino=%.2f eff=%.2f(x%.2f) "
            "score=%.0f(x%.2f) atr=%.2f%%(x%.2f) omega(x%.2f) cvar_cap=%.4f -> %.4f",
            full_kelly, half_kelly, sharpe, sortino, eff_ratio, ratio_scaler,
            current_score, score_scale, atr_pct * 100, vol_scale, om_mult,
            max_risk_capped, risk_final,
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
    bar_ts=None,
    open_trades_risks: list = None,
    max_heat: float = 0.06,
) -> float:
    """
    Full pipeline: Dynamic Kelly x Anti-martingale streak x Time-of-day x
                   Circuit breaker x Portfolio heat check.

    This is the primary entry-point for the live bot and backtester.

    Args:
        recent_trades:      Recent per-trade P&Ls as fraction of capital.
        capital:            Current account equity in INR.
        current_score:      Signal score 0-100.
        atr_pct:            ATR as percentage of price.
        max_risk_pct:       Hard cap on risk per trade (default 2%).
        max_pos_pct:        Hard cap on position value fraction (default 25%).
        bar_ts:             Bar timestamp (pd.Timestamp or datetime) for time-of-day
                            multiplier. Pass None to skip time adjustment.
        open_trades_risks:  List of current open position risk fractions for
                            portfolio heat check. Pass None to skip heat check.
        max_heat:           Maximum total portfolio heat (default 6%).

    Returns:
        risk_fraction (fraction of capital to risk on this trade).
        Returns 0.0 if circuit breaker is tripped or portfolio heat is exceeded.
    """
    try:
        # ── Circuit breaker check ─────────────────────────────────────────────
        if is_trading_halted():
            logger.warning("risk_manager: trading halted by circuit breaker -- returning 0.0")
            return 0.0

        # ── Base Kelly + streak sizing ────────────────────────────────────────
        base        = dynamic_kelly_size(
            recent_trades, capital, current_score, atr_pct,
            max_risk_pct, max_pos_pct,
        )
        streak_mult = get_streak_multiplier()
        combined    = base * streak_mult
        # Re-apply hard cap after streak multiplier
        risk_after_streak = min(combined, max_risk_pct)

        # ── Time-of-day multiplier ────────────────────────────────────────────
        tod_mult = time_of_day_multiplier(bar_ts)
        risk_after_tod = risk_after_streak * tod_mult

        # ── Portfolio heat check ──────────────────────────────────────────────
        if open_trades_risks is not None:
            if not can_add_position(open_trades_risks, risk_after_tod, max_heat):
                logger.warning(
                    "risk_manager: portfolio heat %.4f + new %.4f > max %.4f -- returning 0.0",
                    portfolio_heat(open_trades_risks), risk_after_tod, max_heat,
                )
                return 0.0

        return max(0.0, risk_after_tod)

    except Exception as e:
        logger.debug("risk_manager.compute_position_risk: %s", e)
        return 0.005
