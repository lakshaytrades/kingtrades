"""
dashboard.py — NSE Momentum Groww AI Bot
Performance Dashboard + EOD Reports

Generates:
- Real-time console dashboard
- Daily performance metrics
- Equity curve chart
- Trade-by-trade log
- EOD Telegram report with chart
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

import pandas as pd
import numpy as np

from utils import (
    format_ist_timestamp, get_current_ist_time, get_current_ist_date,
    format_currency
)

logger = logging.getLogger(__name__)
TRADE_DB = "logs/trades/trade_journal.db"


class PerformanceDashboard:
    """Computes and displays trading performance metrics."""

    def get_today_stats(self) -> Dict:
        """Fetch today's trade stats from the journal DB."""
        today = str(get_current_ist_date())
        try:
            conn = sqlite3.connect(TRADE_DB)
            df = pd.read_sql_query(
                "SELECT * FROM trades WHERE entry_time LIKE ?",
                conn, params=[f"%{today}%"]
            )
            conn.close()
        except Exception as e:
            logger.debug(f"DB read error: {e}")
            return {}

        if df.empty:
            return {"date": today, "trades": 0}

        closed = df[df["exit_price"].notna()]
        wins = closed[closed["pnl"] > 0]
        losses = closed[closed["pnl"] <= 0]
        total_pnl = closed["pnl"].sum() if not closed.empty else 0

        return {
            "date": today,
            "total_trades": len(closed),
            "open_trades": len(df) - len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / max(len(closed), 1) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(wins["pnl"].mean(), 2) if len(wins) > 0 else 0,
            "avg_loss": round(losses["pnl"].mean(), 2) if len(losses) > 0 else 0,
            "best_trade": round(closed["pnl"].max(), 2) if not closed.empty else 0,
            "worst_trade": round(closed["pnl"].min(), 2) if not closed.empty else 0,
        }

    def get_historical_stats(self, days: int = 30) -> Dict:
        """Get stats for past N days."""
        try:
            conn = sqlite3.connect(TRADE_DB)
            df = pd.read_sql_query(
                "SELECT * FROM trades WHERE exit_price IS NOT NULL", conn
            )
            conn.close()
        except Exception:
            return {}

        if df.empty:
            return {}

        wins = df[df["pnl"] > 0]
        losses = df[df["pnl"] <= 0]
        total_pnl = df["pnl"].sum()

        returns = df["pnl_pct"].values
        sharpe = (np.mean(returns) / np.std(returns) * np.sqrt(250)
                  if len(returns) > 1 and np.std(returns) > 0 else 0)

        return {
            "total_trades": len(df),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(df) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "sharpe": round(sharpe, 2),
            "avg_pnl_per_trade": round(total_pnl / len(df), 2),
        }

    def print_dashboard(self, risk_manager=None):
        """Print a formatted console dashboard."""
        now = format_ist_timestamp()
        stats = self.get_today_stats()

        print(f"\n{'='*60}")
        print(f"  NSE MOMENTUM BOT DASHBOARD  —  {now}")
        print(f"{'='*60}")

        if stats.get("trades", 0) == 0 and stats.get("total_trades", 0) == 0:
            print("  No trades today yet.")
        else:
            pnl = stats.get("total_pnl", 0)
            pnl_sign = "+" if pnl >= 0 else ""
            print(f"  Daily P&L:    {pnl_sign}{format_currency(pnl)}")
            print(f"  Trades:       {stats.get('total_trades', 0)}")
            print(f"  Win Rate:     {stats.get('win_rate', 0):.1f}%  "
                  f"(W:{stats.get('wins',0)} / L:{stats.get('losses',0)})")
            print(f"  Best Trade:   {format_currency(stats.get('best_trade', 0))}")
            print(f"  Worst Trade:  {format_currency(stats.get('worst_trade', 0))}")

        if risk_manager:
            summary = risk_manager.get_daily_summary()
            print(f"\n  Positions:    {summary.get('open_positions', 0)} open")
            print(f"  Circuit:      {'🚨 ACTIVE' if summary.get('circuit_breaker') else '✅ Off'}")
            print(f"  Paused:       {'⏸ YES' if summary.get('paused') else '▶️ No'}")

        print(f"{'='*60}\n")

    def generate_equity_chart(self, save_path: str = "charts/equity_curve.png") -> Optional[str]:
        """Generate equity curve chart and save to file."""
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            conn = sqlite3.connect(TRADE_DB)
            df = pd.read_sql_query(
                "SELECT entry_time, pnl FROM trades WHERE pnl IS NOT NULL ORDER BY id",
                conn
            )
            conn.close()

            if df.empty:
                return None

            cumulative = df["pnl"].cumsum()
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)

            fig, ax = plt.subplots(figsize=(12, 5), facecolor="#0d1117")
            ax.set_facecolor("#0d1117")
            ax.plot(cumulative.values, color="#26a641", lw=2)
            ax.fill_between(range(len(cumulative)), cumulative.values, 0,
                            where=[v >= 0 for v in cumulative.values],
                            color="#26a641", alpha=0.2)
            ax.fill_between(range(len(cumulative)), cumulative.values, 0,
                            where=[v < 0 for v in cumulative.values],
                            color="#da3633", alpha=0.2)
            ax.axhline(0, color="gray", lw=0.8, linestyle="--")
            ax.set_title("Equity Curve — NSE Momentum Bot", color="white", fontsize=12)
            ax.set_ylabel("Cumulative P&L (₹)", color="gray")
            ax.tick_params(colors="gray")
            for spine in ax.spines.values():
                spine.set_color("#30363d")

            plt.tight_layout()
            plt.savefig(save_path, dpi=120, facecolor="#0d1117", bbox_inches="tight")
            plt.close(fig)
            return save_path

        except Exception as e:
            logger.error(f"Equity chart error: {e}")
            return None

    def format_eod_report(self, risk_manager=None) -> str:
        """Format full EOD text report."""
        today = str(get_current_ist_date())
        stats = self.get_today_stats()
        hist  = self.get_historical_stats()

        pnl = stats.get("total_pnl", 0)
        lines = [
            f"📊 EOD PERFORMANCE REPORT — {today}",
            f"Generated: {format_ist_timestamp()}",
            "=" * 50,
            f"TODAY:",
            f"  P&L:        {format_currency(pnl)} ({'+' if pnl >= 0 else ''}{(pnl/50000*100):.2f}%)",
            f"  Trades:     {stats.get('total_trades', 0)}",
            f"  Win Rate:   {stats.get('win_rate', 0):.1f}%",
            f"  Best:       {format_currency(stats.get('best_trade', 0))}",
            f"  Worst:      {format_currency(stats.get('worst_trade', 0))}",
            "",
            "OVERALL ({} trades):".format(hist.get("total_trades", 0)),
            f"  Win Rate:   {hist.get('win_rate', 0):.1f}%",
            f"  Sharpe:     {hist.get('sharpe', 0):.2f}",
            f"  Total P&L:  {format_currency(hist.get('total_pnl', 0))}",
        ]
        return "\n".join(lines)


if __name__ == "__main__":
    dash = PerformanceDashboard()
    dash.print_dashboard()
    chart = dash.generate_equity_chart()
    if chart:
        print(f"Equity curve saved: {chart}")
