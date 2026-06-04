"""
intraday_var.py — Intraday Value-at-Risk Position Sizing (v29.0)

VaR-based position sizing: the standard risk constraint at every real fund.
Never let a single trade increase portfolio 1-day VaR by > 0.5%.

Historical Simulation VaR (non-parametric, no distribution assumptions):
  1. Collect last N days of 5-min returns for the symbol
  2. Sort returns worst to best
  3. 1% VaR = the return at the 1st percentile (worst 1% of days)
  4. Compute max position size such that loss at VaR doesn't exceed budget

Advantages over simple stop-loss:
  - Accounts for fat tails (gap risk, halt-and-resume)
  - Regime-aware: high-vol symbols get smaller positions automatically
  - Integrates naturally with portfolio-level risk budget

Portfolio VaR budget: DAILY_LOSS_LIMIT_PCT × capital / num_signals
  e.g., 2% daily limit × $93k capital = $1,860 total VaR budget
  With 3 concurrent signals = $620 VaR per trade
"""

import logging
import math
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# How much of the daily loss limit to allocate per new trade
_VAR_BUDGET_FRACTION = 0.33   # 1/3 of daily limit per trade
_VAR_CONFIDENCE = 0.99        # 99% VaR (1% of days worse than this)
_MIN_RETURN_OBSERVATIONS = 20


def get_var_size_multiplier(
    symbol: str,
    df_5m,
    portfolio_capital: float,
    daily_loss_limit_pct: float = 2.0,
    open_positions: int = 0,
) -> Tuple[float, str]:
    """
    Compute position size multiplier from historical VaR.
    Returns (multiplier, reason). Fail-open at 1.0.

    multiplier < 1.0 = reduce size (high VaR stock)
    multiplier > 1.0 = increase size (low VaR stock, well within budget)
    """
    try:
        if df_5m is None or len(df_5m) < _MIN_RETURN_OBSERVATIONS:
            return 1.0, "var:insufficient-data"

        if portfolio_capital <= 0:
            return 1.0, "var:no-capital"

        # Step 1: compute returns
        close_col = "close" if "close" in df_5m.columns else "Close"
        closes = df_5m[close_col].values.astype(float)
        log_returns = np.diff(np.log(closes + 1e-9))

        if len(log_returns) < _MIN_RETURN_OBSERVATIONS:
            return 1.0, "var:too-few-returns"

        # Step 2: historical VaR at 99% confidence (1-day horizon via scaling)
        sorted_returns = np.sort(log_returns)
        percentile_idx = max(0, int(len(sorted_returns) * (1 - _VAR_CONFIDENCE)) - 1)
        var_return = float(sorted_returns[percentile_idx])  # negative number = loss

        if var_return >= 0:
            # Never loses? Suspicious — use small negative
            var_return = -0.005

        var_pct = abs(var_return)  # as a positive fraction

        # Scale to 1-day horizon (multiply by sqrt(bars_per_day / bars_in_sample))
        # We have 5-min bars, 78 per day; simple scaling
        bar_count = len(log_returns)
        bars_per_day = min(78, bar_count)
        scale = math.sqrt(bars_per_day)
        daily_var_pct = var_pct * scale

        # Step 3: compute VaR budget per trade
        n_active = max(1, open_positions + 1)  # +1 for this new position
        daily_budget = portfolio_capital * daily_loss_limit_pct / 100.0
        per_trade_budget = daily_budget * _VAR_BUDGET_FRACTION / n_active

        # Step 4: max position value such that VaR loss <= budget
        # loss = position_value × daily_var_pct
        max_notional = per_trade_budget / max(daily_var_pct, 0.001)

        # Reference notional: assume base position = 1% of capital
        reference_notional = portfolio_capital * 0.01
        if reference_notional <= 0:
            return 1.0, "var:no-reference"

        multiplier = max_notional / reference_notional

        # Clamp to reasonable range
        multiplier = max(0.25, min(2.0, multiplier))

        return round(multiplier, 3), (
            f"var:{daily_var_pct*100:.2f}%1d-VaR "
            f"budget=${per_trade_budget:.0f} → {multiplier:.2f}x"
        )

    except Exception as e:
        logger.debug(f"[intraday_var] {symbol}: {e}")
        return 1.0, "var:error:fail-open"


def get_portfolio_var(
    open_positions_values: list,
    returns_matrix: Optional[np.ndarray] = None,
) -> float:
    """
    Estimate current portfolio 1-day VaR as a fraction of total value.
    Simple: sum individual VaRs (conservative, ignores diversification).
    """
    try:
        if not open_positions_values:
            return 0.0
        # Assume 2% daily VaR per position as default
        total = sum(abs(v) * 0.02 for v in open_positions_values)
        return total
    except Exception:
        return 0.0
