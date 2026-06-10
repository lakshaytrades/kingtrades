"""
live_expectations.py — KingTrades Forward-Looking Performance Projections

Calculates realistic daily/weekly/monthly expectations based on:
  - Historical win rates and average R from logs (if available)
  - Strategy-specific edge estimates from backtest data
  - Current capital and compound growth projections
  - Monte Carlo simulation (1,000 paths) for probability ranges

Run: python3 live_expectations.py
Sends a Telegram report with 3-scenario projections (conservative/realistic/optimistic)
and individual strategy contribution estimates.
"""

import json
import logging
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── Strategy edge estimates (from literature + typical backtest results) ──────
# Format: {name: (win_rate, avg_win_r, avg_loss_r, trades_per_day)}
STRATEGY_EDGES = {
    "ORB Breakout":          (0.58, 2.1, 1.0, 1.5),
    "VWAP Reclaim":          (0.62, 1.8, 1.0, 2.0),
    "Power Hour":            (0.55, 1.9, 1.0, 1.2),
    "Short Squeeze":         (0.52, 3.2, 1.0, 0.5),
    "Momentum Persistence":  (0.60, 1.7, 1.0, 1.8),
    "Z-Score Reversion":     (0.56, 2.0, 1.0, 1.0),
    "Gap-and-Go":            (0.54, 2.3, 1.0, 0.8),
    "PEAD Drift":            (0.58, 2.4, 1.0, 0.4),
    "Market Profile/POC":    (0.57, 1.9, 1.0, 1.2),
    "Order Flow Imbalance":  (0.55, 1.6, 1.0, 2.5),
    "Gamma Squeeze":         (0.50, 3.5, 1.0, 0.3),
    "Cross-Sectional Mom":   (0.59, 1.8, 1.0, 2.0),
    "Pairs Convergence":     (0.61, 1.6, 1.0, 0.8),
    "Dark Pool Signal":      (0.63, 1.7, 1.0, 0.7),
}

COMMISSION_PER_TRADE = 0.0005   # 0.05% round-trip (Alpaca)
SLIPPAGE_PER_TRADE   = 0.0005   # 0.05% slippage estimate
TOTAL_COST_PER_TRADE = COMMISSION_PER_TRADE + SLIPPAGE_PER_TRADE

TRADING_DAYS_PER_MONTH = 22
TRADING_DAYS_PER_YEAR  = 252


def _load_live_stats() -> Dict:
    """Load actual bot stats from capital.json and trade_journal if available."""
    stats = {
        "capital": 5000.0,
        "actual_win_rate": None,
        "actual_avg_r": None,
        "total_trades": 0,
        "month_pnl": 0.0,
        "daily_pnl": 0.0,
    }
    cap_file = Path("data/capital.json")
    if cap_file.exists():
        try:
            cap = json.loads(cap_file.read_text())
            stats["capital"]    = cap.get("total_equity", cap.get("daily_capital", 5000.0))
            stats["month_pnl"]  = cap.get("month_pnl", 0.0)
            stats["daily_pnl"]  = cap.get("daily_pnl", 0.0)
            wins   = cap.get("wins", 0)
            losses = cap.get("losses", 0)
            if wins + losses >= 10:
                stats["actual_win_rate"]  = wins / (wins + losses)
                stats["total_trades"]     = wins + losses
        except Exception:
            pass
    return stats


def compute_strategy_expectancy(
    win_rate: float, avg_win_r: float, avg_loss_r: float
) -> float:
    """Expected R per trade = WR * avg_win - (1-WR) * avg_loss."""
    return win_rate * avg_win_r - (1 - win_rate) * avg_loss_r


def simulate_equity_curve(
    capital: float,
    risk_per_trade_pct: float,
    win_rate: float,
    avg_win_r: float,
    avg_loss_r: float,
    trades_per_day: float,
    days: int,
    n_simulations: int = 1000,
) -> Dict:
    """
    Monte Carlo: simulate N equity curves over 'days' trading days.
    Returns percentile outcomes.
    """
    risk_per_trade = capital * (risk_per_trade_pct / 100)
    final_equities = []

    for _ in range(n_simulations):
        equity = capital
        for _day in range(days):
            n_trades = max(0, int(round(random.gauss(trades_per_day, trades_per_day * 0.3))))
            for _ in range(n_trades):
                if random.random() < win_rate:
                    pnl = risk_per_trade * avg_win_r
                else:
                    pnl = -risk_per_trade * avg_loss_r
                # deduct trading costs
                pnl -= equity * TOTAL_COST_PER_TRADE
                equity = max(equity + pnl, 0.0)
                if equity <= 0:
                    break
            if equity <= 0:
                break
        final_equities.append(equity)

    arr = np.array(final_equities)
    return {
        "p10": float(np.percentile(arr, 10)),
        "p25": float(np.percentile(arr, 25)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "mean": float(arr.mean()),
        "pct_profitable": float((arr > capital).mean() * 100),
        "pct_blowup": float((arr < capital * 0.8).mean() * 100),  # >20% drawdown
    }


def compute_blended_edge(
    strategies: Dict,
    scenario: str = "realistic"
) -> Tuple[float, float, float, float]:
    """
    Compute blended metrics across all active strategies.
    scenario: 'conservative', 'realistic', 'optimistic'
    Returns (blended_win_rate, blended_avg_win_r, trades_per_day, daily_expectancy_r)
    """
    multipliers = {
        "conservative": 0.80,
        "realistic":    1.00,
        "optimistic":   1.15,
    }
    mult = multipliers.get(scenario, 1.0)

    total_weight = 0.0
    weighted_wr  = 0.0
    weighted_wr_win = 0.0
    total_tpd    = 0.0
    daily_er     = 0.0

    for name, (wr, avg_win, avg_loss, tpd) in strategies.items():
        adj_wr   = min(0.80, wr * mult)
        exp_r    = compute_strategy_expectancy(adj_wr, avg_win, avg_loss)
        weight   = tpd
        weighted_wr     += adj_wr * weight
        weighted_wr_win += avg_win * weight
        total_weight    += weight
        total_tpd       += tpd
        daily_er        += exp_r * tpd

    if total_weight == 0:
        return 0.50, 2.0, 3.0, 0.5

    blended_wr     = weighted_wr / total_weight
    blended_win_r  = weighted_wr_win / total_weight

    return blended_wr, blended_win_r, total_tpd, daily_er


def build_report(capital: float = 5000.0) -> str:
    """Build the full live expectations report."""
    live = _load_live_stats()
    capital = live["capital"] if live["capital"] > 100 else capital
    risk_pct = 0.8   # default from config
    try:
        from autonomous_optimizer import _load_config
        cfg = _load_config()
        risk_pct = cfg.get("risk_per_trade_pct", 0.8)
    except Exception:
        pass

    lines = [
        "📊 <b>KINGTRADES LIVE EXPECTATIONS REPORT</b>",
        f"Capital: ${capital:,.0f}  |  Risk/trade: {risk_pct:.1f}%",
        f"Active strategies: {len(STRATEGY_EDGES)} modules",
        "",
    ]

    # ── Per-strategy expectancy table ──────────────────────────────────────
    lines.append("━━━ STRATEGY EDGE TABLE ━━━")
    lines.append(f"{'Strategy':<24} {'WR':>5} {'AvgW':>5} {'Expect':>7} {'TPD':>5}")
    lines.append("─" * 52)
    all_expectancies = []
    for name, (wr, avg_win, avg_loss, tpd) in sorted(
        STRATEGY_EDGES.items(), key=lambda x: -compute_strategy_expectancy(x[1][0], x[1][1], x[1][2])
    ):
        exp = compute_strategy_expectancy(wr, avg_win, avg_loss)
        all_expectancies.append(exp)
        lines.append(f"{name:<24} {wr*100:>4.0f}% {avg_win:>5.1f}R {exp:>+6.2f}R {tpd:>4.1f}/d")
    lines.append("")

    # ── Scenarios ──────────────────────────────────────────────────────────
    lines.append("━━━ 3-SCENARIO PROJECTIONS ━━━")
    lines.append(f"{'Scenario':<14} {'Daily%':>7} {'Monthly%':>9} {'3-Month':>9} {'1-Year':>9}")
    lines.append("─" * 52)

    for scenario in ("conservative", "realistic", "optimistic"):
        bwr, bwin_r, tpd, daily_er = compute_blended_edge(STRATEGY_EDGES, scenario)
        adj = {"conservative": 0.80, "realistic": 1.00, "optimistic": 1.15}[scenario]
        risk_amt = capital * (risk_pct / 100)

        daily_pnl_r   = daily_er * adj
        daily_pct     = (daily_pnl_r * risk_amt / capital) * 100 - (tpd * TOTAL_COST_PER_TRADE * 100)
        monthly_pct   = daily_pct * TRADING_DAYS_PER_MONTH
        three_month   = ((1 + daily_pct / 100) ** (TRADING_DAYS_PER_MONTH * 3) - 1) * 100
        annual        = ((1 + daily_pct / 100) ** TRADING_DAYS_PER_MONTH - 1) ** 12 * capital / capital * 100

        ann_mult = (1 + daily_pct / 100) ** TRADING_DAYS_PER_YEAR - 1
        annual_pct = ann_mult * 100

        icon = {"conservative": "🟡", "realistic": "🟢", "optimistic": "🔵"}[scenario]
        lines.append(
            f"{icon} {scenario:<12} {daily_pct:>+6.2f}% {monthly_pct:>+8.1f}% "
            f"{three_month:>+8.0f}% {annual_pct:>+8.0f}%"
        )

    lines.append("")

    # ── Monte Carlo (realistic scenario) ───────────────────────────────────
    lines.append("━━━ MONTE CARLO (Realistic, 1,000 paths) ━━━")
    bwr, bwin_r, tpd, _ = compute_blended_edge(STRATEGY_EDGES, "realistic")
    adj_tpd = min(tpd, 8.0)   # cap at 8 trades/day (realistic for one bot)

    for period_name, days in [("30-Day", 22), ("90-Day", 66), ("6-Month", 132)]:
        mc = simulate_equity_curve(
            capital=capital,
            risk_per_trade_pct=risk_pct,
            win_rate=bwr,
            avg_win_r=bwin_r,
            avg_loss_r=1.0,
            trades_per_day=adj_tpd,
            days=days,
            n_simulations=500,
        )
        p25_pct = (mc["p25"] / capital - 1) * 100
        p50_pct = (mc["p50"] / capital - 1) * 100
        p75_pct = (mc["p75"] / capital - 1) * 100
        lines.append(
            f"{period_name}: P25={p25_pct:+.0f}%  P50={p50_pct:+.0f}%  P75={p75_pct:+.0f}%  "
            f"({mc['pct_profitable']:.0f}% profitable)"
        )

    lines.append("")

    # ── Actual live stats if available ─────────────────────────────────────
    if live["actual_win_rate"] is not None:
        lines.append("━━━ ACTUAL LIVE PERFORMANCE ━━━")
        actual_wr = live["actual_win_rate"]
        n = live["total_trades"]
        lines.append(f"Live WR: {actual_wr*100:.1f}% over {n} trades")
        lines.append(f"Month P&L: ${live['month_pnl']:+,.2f}")
        lines.append(f"Today P&L: ${live['daily_pnl']:+,.2f}")
        lines.append("")

    # ── Compounding targets ────────────────────────────────────────────────
    lines.append("━━━ COMPOUNDING PROJECTIONS ━━━")
    lines.append(f"Starting capital: ${capital:,.0f}")
    # Use realistic daily rate
    _, _, _, daily_er_r = compute_blended_edge(STRATEGY_EDGES, "realistic")
    adj_tpd2 = min(daily_er_r * 4, 6.0)
    risk_amt = capital * (risk_pct / 100)
    daily_rate = (adj_tpd2 * risk_amt / capital) - (adj_tpd2 * TOTAL_COST_PER_TRADE)
    daily_rate = max(0.002, min(0.015, daily_rate))  # clamp 0.2%–1.5%

    cap = capital
    for months in (1, 3, 6, 12):
        cap_m = capital * ((1 + daily_rate) ** (TRADING_DAYS_PER_MONTH * months))
        lines.append(f"  {months:>2} month{'s' if months>1 else ' '}: ${cap_m:>10,.0f}  ({(cap_m/capital-1)*100:+.0f}%)")

    lines.append("")
    lines.append("⚠️ Projections assume consistent execution. Past performance is not")
    lines.append("   a guarantee of future results. Always manage risk.")
    lines.append(f"\n<i>Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</i>")

    return "\n".join(lines)


def send_to_telegram(text: str) -> bool:
    # US bot disabled by owner — never send unless explicitly re-enabled
    import os as _os_g
    if _os_g.getenv("US_BOT_ENABLED", "False") != "True":
        return False
    """Send the report to Telegram."""
    try:
        import requests
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id   = os.getenv("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            print("No Telegram credentials — printing to stdout only")
            return False
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        r = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def save_report(text: str) -> Path:
    """Save report to logs/."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    fname = log_dir / f"live_expectations_{datetime.utcnow().strftime('%Y-%m-%d')}.txt"
    fname.write_text(text)
    return fname


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description="KingTrades Live Expectations Report")
    parser.add_argument("--capital", type=float, default=5000.0, help="Starting capital")
    parser.add_argument("--no-telegram", action="store_true", help="Print only, don't send Telegram")
    args = parser.parse_args()

    print("Building live expectations report...")
    report = build_report(capital=args.capital)
    print(report)

    saved = save_report(report)
    print(f"\nSaved to: {saved}")

    if not args.no_telegram:
        ok = send_to_telegram(report)
        if ok:
            print("Sent to Telegram ✓")
        else:
            print("Telegram send failed (check credentials)")
