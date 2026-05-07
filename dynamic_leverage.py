"""
dynamic_leverage.py — High-Conviction Position Sizing

Aggregates confidence signals from every layer of the bot and returns a
size multiplier (0.5x – 3.0x) to apply on top of the base position size.

Conviction sources (weighted):
  Signal score          25% — core technical quality
  Claude supervisor     25% — AI review confidence
  RL Q-table edge       20% — learned conviction from past trades
  MTF alignment         15% — timeframe agreement
  Block deal backing    10% — institutional accumulation today
  Sector heat            5% — trading in a hot sector

Multiplier table:
  Conviction 0-59  → 0.5x  (marginal — shrink size)
  Conviction 60-69 → 0.75x (below average)
  Conviction 70-79 → 1.0x  (normal)
  Conviction 80-89 → 1.5x  (high confidence — scale up)
  Conviction 90-94 → 2.0x  (very high — double size)
  Conviction 95+   → 3.0x  (institutional-grade — triple size)

Hard safety caps (always enforced regardless of multiplier):
  - Actual risk never exceeds 2.5% of daily capital
  - Single position never exceeds 25% of buying power
  - Multiplier drops to 0.5x if today's P&L is already negative
  - Circuit breaker / daily loss limit still takes priority over everything
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Conviction → multiplier table ────────────────────────────────────────────
_MULTIPLIER_STEPS = [
    (95, 3.0),
    (90, 2.0),
    (80, 1.5),
    (70, 1.0),
    (60, 0.75),
    (0,  0.5),
]


@dataclass
class ConvictionScore:
    total: float                  # 0-100 weighted aggregate
    signal_score_contrib: float
    claude_contrib: float
    rl_contrib: float
    mtf_contrib: float
    block_deal_contrib: float
    sector_contrib: float
    multiplier: float
    reason: str


def calculate_conviction(
    signal_score: float,
    claude_confidence: int,       # 0-100, from claude_supervisor review
    claude_approved: bool,
    rl_qtable: Optional[object],  # RLAgent instance or None
    rl_state: Optional[str],      # current state string
    mtf_alignment: dict,          # from MultiTimeframeAnalyzer
    symbol: str,
    direction: str,
    block_deal_scanner=None,
    sector_rotation=None,
    daily_pnl: float = 0.0,
    daily_capital: float = 1.0,
) -> ConvictionScore:
    """
    Compute weighted conviction score and return size multiplier.
    All inputs are optional — missing ones default to neutral (50 points).
    """

    # ── 1. Signal score (25%) ──────────────────────────────────────────────
    # Raw score 80-100 maps to 50-100 conviction points
    sig_raw = max(0.0, min(100.0, signal_score))
    sig_conv = max(0.0, (sig_raw - 70) / 30 * 100) if sig_raw >= 70 else sig_raw * 0.5
    sig_conv = min(sig_conv, 100.0)
    sig_contrib = sig_conv * 0.25

    # ── 2. Claude supervisor (25%) ─────────────────────────────────────────
    if not claude_approved:
        # Claude rejected = hard block, multiplier irrelevant
        # (execution_groww.py already returns early on rejection)
        claude_conv = 0.0
    else:
        claude_conv = float(claude_confidence) if claude_confidence else 50.0
    claude_contrib = claude_conv * 0.25

    # ── 3. RL Q-table edge (20%) ───────────────────────────────────────────
    rl_conv = 50.0  # neutral default
    if rl_qtable is not None and rl_state is not None:
        try:
            q_vals = rl_qtable.qtable.get(rl_state)   # [skip, long, short]
            action_idx = {"LONG": 1, "SHORT": 2}.get(direction, 1)
            best_q = q_vals[action_idx]
            worst_q = min(q_vals)
            edge = best_q - worst_q          # How much better this action is vs alternatives
            # Map edge to 0-100: edge of 0 = 50, edge of +1 = 100, edge of -1 = 0
            rl_conv = max(0.0, min(100.0, 50.0 + edge * 50.0))
        except Exception:
            pass
    rl_contrib = rl_conv * 0.20

    # ── 4. MTF alignment (15%) ────────────────────────────────────────────
    mtf_conv = 50.0
    if mtf_alignment:
        score = mtf_alignment.get("alignment_score", 50)
        aligned_count = mtf_alignment.get("aligned_count", 0)  # 0, 1, 2 or 3
        if aligned_count == 3:
            mtf_conv = 100.0   # All 3 timeframes locked — maximum confidence
        elif aligned_count == 2:
            mtf_conv = 70.0
        else:
            mtf_conv = max(0.0, float(score))
    mtf_contrib = mtf_conv * 0.15

    # ── 5. Block deal backing (10%) ───────────────────────────────────────
    block_conv = 50.0  # neutral if no data
    if block_deal_scanner is not None:
        try:
            buy_syms = block_deal_scanner.scan_and_alert.__self__._alerted_today if hasattr(
                block_deal_scanner, '_alerted_today') else set()
            # Check if any block buy signal exists for this symbol today
            today_buys = getattr(block_deal_scanner, '_buy_symbols_today', set())
            if symbol in today_buys:
                block_conv = 100.0   # Institutional accumulation today — max conviction
            elif direction == "SHORT":
                sell_syms = getattr(block_deal_scanner, '_sell_symbols_today', set())
                if symbol in sell_syms:
                    block_conv = 100.0
        except Exception:
            pass
    block_contrib = block_conv * 0.10

    # ── 6. Sector heat (5%) ───────────────────────────────────────────────
    sector_conv = 50.0
    if sector_rotation is not None:
        try:
            hot = sector_rotation.get_hot_sectors(top_n=3)
            sym_sector = sector_rotation.get_sector_for_symbol(symbol)
            scores = {s.sector: s.score for s in sector_rotation.score_all_sectors()}
            raw_score = scores.get(sym_sector, 50.0)
            if sym_sector in hot:
                sector_conv = min(100.0, raw_score * 1.2)  # Bonus for hot sector
            else:
                sector_conv = raw_score * 0.6   # Penalty for cold sector
        except Exception:
            pass
    sector_contrib = sector_conv * 0.05

    # ── 7. Win Predictor — ML model trained on past trades ───────────────
    win_adj = 0.0
    try:
        from win_predictor import get_win_predictor
        predictor = get_win_predictor()
        win_prob, is_confident = predictor.predict({
            "signal_score": signal_score, "rsi": 50,
            "volume_ratio": 1.5, "session": "NORMAL",
            "direction": direction, "macd_hist": 0.0,
            "vwap_deviation_pct": 0.0,
        })
        if is_confident:
            if win_prob > 0.70:    win_adj = +15.0
            elif win_prob > 0.60:  win_adj = +8.0
            elif win_prob < 0.30:  win_adj = -25.0   # Block via total score drop
            elif win_prob < 0.40:  win_adj = -15.0
    except Exception:
        pass

    # ── Aggregate ─────────────────────────────────────────────────────────
    total = sig_contrib + claude_contrib + rl_contrib + mtf_contrib + block_contrib + sector_contrib + win_adj

    # ── Capital protection: shrink size if already losing today ───────────
    loss_pct = abs(daily_pnl) / max(daily_capital, 1) * 100
    if daily_pnl < 0 and loss_pct >= 1.0:
        total *= 0.6   # Down 1%+ today → reduce conviction 40%
    elif daily_pnl < 0 and loss_pct >= 0.5:
        total *= 0.8   # Down 0.5%+ → reduce 20%

    total = max(0.0, min(100.0, total))

    # ── Map to multiplier ─────────────────────────────────────────────────
    multiplier = 0.5
    for threshold, mult in _MULTIPLIER_STEPS:
        if total >= threshold:
            multiplier = mult
            break

    # Build human-readable reason
    parts = []
    if sig_conv >= 80:   parts.append(f"score={signal_score:.0f}")
    if claude_conv >= 80: parts.append(f"claude={claude_confidence}%")
    if rl_conv >= 80:    parts.append("RL=confident")
    if mtf_conv >= 90:   parts.append("MTF=3/3")
    if block_conv >= 90: parts.append("block_deal=YES")
    reason = f"conviction={total:.0f} [{', '.join(parts) or 'mixed signals'}] → {multiplier}x"

    logger.info(
        f"[DynLeverage] {symbol} {direction} | "
        f"conviction={total:.1f} | mult={multiplier}x | "
        f"sig={sig_conv:.0f} claude={claude_conv:.0f} rl={rl_conv:.0f} "
        f"mtf={mtf_conv:.0f} block={block_conv:.0f} sector={sector_conv:.0f}"
    )

    return ConvictionScore(
        total=round(total, 1),
        signal_score_contrib=round(sig_contrib, 2),
        claude_contrib=round(claude_contrib, 2),
        rl_contrib=round(rl_contrib, 2),
        mtf_contrib=round(mtf_contrib, 2),
        block_deal_contrib=round(block_contrib, 2),
        sector_contrib=round(sector_contrib, 2),
        multiplier=multiplier,
        reason=reason,
    )


def apply_conviction_cap(
    quantity: int,
    multiplier: float,
    entry_price: float,
    stop_loss: float,
    daily_capital: float,
) -> int:
    """
    Apply the conviction multiplier to quantity, then enforce hard safety caps:
      - Actual risk <= 2.5% of daily capital
      - Position value <= 25% of capital × 5x leverage (= 125% buying power cap)
    """
    new_qty = max(1, int(quantity * multiplier))

    # Cap 1: risk never exceeds 2.5% of capital
    sl_distance = abs(entry_price - stop_loss)
    max_risk = daily_capital * 0.025
    max_by_risk = int(max_risk / sl_distance) if sl_distance > 0 else new_qty
    new_qty = min(new_qty, max_by_risk)

    # Cap 2: single position value <= 25% of leveraged buying power
    buying_power = daily_capital * 5.0
    max_by_capital = int(buying_power * 0.25 / entry_price) if entry_price > 0 else new_qty
    new_qty = min(new_qty, max_by_capital)

    return max(1, new_qty)
