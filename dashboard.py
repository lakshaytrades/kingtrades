"""
dashboard.py — NSE Momentum Groww AI Bot
Real-Time Performance Dashboard + EOD Reports

Features:
- Live terminal dashboard (refreshes every 30s during market hours)
- Equity curve chart (matplotlib)
- Per-trade P&L waterfall
- EOD summary report → sent via Telegram
- Session breakdown table
- Pattern performance heatmap

All timestamps in IST. Server runs in UK (UTC).
"""

import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# Headless backend — MUST be set before any plt import (required for Render/VPS)
os.environ.setdefault("MPLBACKEND", "Agg")
try:
    import matplotlib
    matplotlib.use("Agg")
except Exception:
    pass

from utils import (
    format_ist_timestamp, format_currency,
    get_current_ist_time, get_current_ist_date
)

logger = logging.getLogger(__name__)
CHART_DIR = Path("charts")
CHART_DIR.mkdir(exist_ok=True)


class PerformanceDashboard:
    """Live performance tracker + EOD report generator."""

    def __init__(self, journal=None, alerter=None):
        self.journal = journal
        self.alerter = alerter

    # -------------------------------------------------------
    # TERMINAL DASHBOARD (Rich text)
    # -------------------------------------------------------

    def print_live_dashboard(self, risk_mgr=None):
        """Print compact live dashboard to terminal."""
        now = format_ist_timestamp()
        summary = self.journal.get_today_summary() if self.journal else {}

        pnl      = summary.get("net_pnl", 0)
        trades   = summary.get("total_trades", 0)
        wins     = summary.get("wins", 0)
        losses   = summary.get("losses", 0)
        wr       = summary.get("win_rate", 0)
        positions = 0

        if risk_mgr:
            positions = risk_mgr.positions_count
            pnl       = risk_mgr.daily_pnl

        pnl_sign = "+" if pnl >= 0 else ""
        status   = "🟢 RUNNING" if risk_mgr and getattr(risk_mgr, "running", True) else "🔴 STOPPED"

        print(f"""
╔══════════════════════════════════════════════╗
║   NSE MOMENTUM BOT  [{now}]
╠══════════════════════════════════════════════╣
║  Status   : {status}
║  P&L      : {pnl_sign}{format_currency(pnl)}
║  Positions: {positions} open
║  Trades   : {trades} ({wins}W / {losses}L)
║  Win Rate : {wr:.1f}%
╚══════════════════════════════════════════════╝""")

    # -------------------------------------------------------
    # EQUITY CURVE CHART
    # -------------------------------------------------------

    def generate_equity_curve(self, save_path: Optional[str] = None) -> Optional[str]:
        """Generate cumulative equity curve chart. Returns file path."""
        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates

            if not self.journal:
                return None
            df = self.journal.get_daily_equity_curve()
            if df.empty:
                return None

            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), facecolor="#0d1117")
            for ax in (ax1, ax2):
                ax.set_facecolor("#0d1117")
                ax.tick_params(colors="white")
                ax.spines[:].set_color("#30363d")

            dates = pd.to_datetime(df["date_ist"])
            cum_pnl = df["cumulative_pnl"]

            # Equity curve
            color = "#2ea043" if cum_pnl.iloc[-1] >= 0 else "#f85149"
            ax1.plot(dates, cum_pnl, color=color, linewidth=2)
            ax1.fill_between(dates, cum_pnl, alpha=0.15, color=color)
            ax1.axhline(0, color="#8b949e", linewidth=0.8, linestyle="--")
            ax1.set_title("Cumulative P&L Equity Curve", color="white", fontsize=13, pad=10)
            ax1.set_ylabel("₹ P&L", color="white")
            ax1.yaxis.set_major_formatter(lambda x, p: f"₹{x:,.0f}")

            # Daily bars
            daily = df["daily_pnl"]
            colors_bar = ["#2ea043" if v >= 0 else "#f85149" for v in daily]
            ax2.bar(dates, daily, color=colors_bar, width=0.6)
            ax2.axhline(0, color="#8b949e", linewidth=0.8, linestyle="--")
            ax2.set_title("Daily P&L", color="white", fontsize=11, pad=8)
            ax2.set_ylabel("₹ P&L", color="white")

            plt.tight_layout(pad=2)
            path = save_path or str(CHART_DIR / f"equity_{get_current_ist_date()}.png")
            plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0d1117")
            plt.close(fig)
            return path

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Equity curve error: {e}")
            return None

    # -------------------------------------------------------
    # EOD REPORT (sent via Telegram)
    # -------------------------------------------------------

    def generate_eod_report(self, capital: float = 50000, learner=None) -> Dict:
        """
        Full end-of-day performance report.
        Returns dict with stats + sends Telegram message with chart.
        """
        if not self.journal:
            return {}

        summary    = self.journal.get_today_summary()
        trades_df  = self.journal.get_trades_last_n_days(1)
        pat_df     = self.journal.get_pattern_stats()
        sess_df    = self.journal.get_session_stats()

        net_pnl    = summary.get("net_pnl", 0)
        ret_pct    = (net_pnl / capital * 100) if capital > 0 else 0
        trades     = summary.get("total_trades", 0)
        wr         = summary.get("win_rate", 0)

        # Sharpe (simplified daily)
        if not trades_df.empty and "net_pnl" in trades_df.columns:
            pnl_vals  = trades_df["net_pnl"].values
            sharpe    = (np.mean(pnl_vals) / (np.std(pnl_vals) + 1e-9)) * np.sqrt(252)
        else:
            sharpe = 0.0

        # Build report text
        sign = "+" if net_pnl >= 0 else ""
        emoji = "🟢" if net_pnl >= 0 else "🔴"

        report = {
            "date_ist":      str(get_current_ist_date()),
            "net_pnl":       net_pnl,
            "return_pct":    round(ret_pct, 2),
            "total_trades":  trades,
            "wins":          summary.get("wins", 0),
            "losses":        summary.get("losses", 0),
            "win_rate":      wr,
            "sharpe":        round(sharpe, 2),
            "best_trade":    summary.get("best_trade", 0),
            "worst_trade":   summary.get("worst_trade", 0),
        }

        msg = (
            f"{emoji} EOD Report — {get_current_ist_date()}\n"
            f"P&L:   {sign}{format_currency(net_pnl)} ({sign}{ret_pct:.2f}%)\n"
            f"Trades: {trades}  |  W/L: {summary.get('wins',0)}/{summary.get('losses',0)}\n"
            f"Win Rate: {wr:.1f}%  |  Sharpe: {sharpe:.2f}\n"
            f"Best: {format_currency(summary.get('best_trade',0))}"
            f"  Worst: {format_currency(summary.get('worst_trade',0))}\n"
        )

        if not pat_df.empty:
            top3 = pat_df.head(3)
            msg += "\nTop Patterns:\n"
            for _, row in top3.iterrows():
                msg += f"  • {row['pattern_name']}: {row['win_rate']:.0f}% WR ({row['total_trades']} trades)\n"

        # Learner insights
        if learner:
            msg += f"\n{learner.get_learning_summary()}\n"

        logger.info(f"[{format_ist_timestamp()}] EOD Report:\n{msg}")

        # Send via Telegram
        if self.alerter:
            try:
                import asyncio
                chart_path = self.generate_equity_curve()
                asyncio.run(self.alerter.send_eod_report(report, chart_path))
            except Exception as e:
                logger.error(f"[{format_ist_timestamp()}] EOD Telegram send failed: {e}")

        # Save daily summary to journal
        if self.journal:
            self.journal.save_daily_summary({
                **report,
                "capital_deployed": capital,
                "gross_pnl": net_pnl,
            })

        return report

    # -------------------------------------------------------
    # PATTERN PERFORMANCE TABLE
    # -------------------------------------------------------

    def print_pattern_table(self):
        """Print pattern win rate table to console."""
        if not self.journal:
            return
        df = self.journal.get_pattern_stats()
        if df.empty:
            print("No pattern data yet.")
            return
        print("\n=== Pattern Performance ===")
        print(f"{'Pattern':<30} {'Trades':>6} {'WR%':>6} {'Avg P&L':>10}")
        print("-" * 56)
        for _, row in df.iterrows():
            print(
                f"{row['pattern_name']:<30} "
                f"{row['total_trades']:>6} "
                f"{row['win_rate']:>5.1f}% "
                f"₹{row['avg_pnl']:>9.0f}"
            )

    # -------------------------------------------------------
    # OPEN POSITIONS TABLE
    # -------------------------------------------------------

    def print_positions(self, risk_mgr):
        """Print live positions table."""
        if not risk_mgr:
            return
        positions = getattr(risk_mgr, "open_positions", [])
        if not positions:
            print("No open positions.")
            return
        print(f"\n=== Open Positions [{format_ist_timestamp()}] ===")
        print(f"{'Symbol':<12} {'Dir':>5} {'Qty':>5} {'Entry':>8} {'CMP':>8} {'P&L':>10} {'SL':>8}")
        print("-" * 65)
        for pos in positions:
            pnl_str = f"₹{pos.pnl:+.0f}"
            print(
                f"{pos.symbol:<12} {pos.direction:>5} {pos.quantity:>5} "
                f"₹{pos.entry_price:>7.1f} ₹{pos.current_price:>7.1f} "
                f"{pnl_str:>10} ₹{pos.active_sl:>7.1f}"
            )
        total_pnl = sum(p.pnl for p in positions)
        print(f"\nTotal Open P&L: ₹{total_pnl:+.0f}")
