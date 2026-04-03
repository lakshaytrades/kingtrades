"""
continuous_learner.py — NSE Momentum Groww AI Bot
24/7 Continuous Learning Scheduler

The bot NEVER stops learning — even when the market is closed.
This is what gives it the "18 years of experience" that compounds over time.

Schedule:
  03:30 PM IST  Market closes → EOD data download begins
  04:00 PM IST  Self-learning cycle (trade analysis + parameter adaptation)
  04:30 PM IST  AI brain EOD trade review (lessons extracted)
  05:00 PM IST  Walk-forward trainer runs on today's fresh data
  07:00 PM IST  Weekly review (Sundays only)
  08:00 PM IST  Economic calendar refresh
  10:00 PM IST  Global market snapshot (US markets open at ~9:30 PM IST)
  01:00 AM IST  US markets close → overnight analysis starts
  06:00 AM IST  Asian markets data fetch
  08:00 AM IST  Full pre-market intelligence report generated
  08:30 AM IST  Morning brief sent to Telegram
  08:45 AM IST  TOTP login to Groww → token refresh

Daily learning targets:
  - Download fresh 5m/15m/1h/daily candles for all watchlist stocks
  - Update pattern win rates from today's trades
  - Adjust RSI thresholds, min score, session multipliers
  - Disable chronic losing patterns
  - Update seasonal/calendar intelligence
  - Generate tomorrow's trading plan with AI
"""

import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from utils import (
    format_ist_timestamp, get_current_ist_time, get_current_ist_date,
    is_market_open_ist, is_market_day_ist, setup_logging
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class ScheduledTask:
    """A task that runs at a specific IST time daily (or on specific weekdays)."""
    def __init__(self, name: str, hour_ist: int, minute_ist: int,
                 func: Callable, weekdays: Optional[List[int]] = None,
                 run_on_holidays: bool = False):
        self.name          = name
        self.hour_ist      = hour_ist
        self.minute_ist    = minute_ist
        self.func          = func
        self.weekdays      = weekdays    # None = every day, [0,1,2,3,4] = Mon-Fri
        self.run_on_holidays = run_on_holidays
        self.last_run_date: Optional[str] = None
        self.run_count     = 0
        self.last_error:   Optional[str] = None

    def is_due(self, now_ist: datetime) -> bool:
        today_str = str(now_ist.date())
        if self.last_run_date == today_str:
            return False
        if self.weekdays and now_ist.weekday() not in self.weekdays:
            return False
        if now_ist.hour == self.hour_ist and now_ist.minute == self.minute_ist:
            return True
        # Allow 2-minute late window (in case scheduler wakes up late)
        target = now_ist.replace(hour=self.hour_ist, minute=self.minute_ist, second=0)
        diff   = (now_ist - target).total_seconds()
        return 0 <= diff <= 120

    def run(self):
        logger.info(f"[{format_ist_timestamp()}] ⏰ Task: {self.name}")
        try:
            self.func()
            self.run_count    += 1
            self.last_run_date = str(get_current_ist_date())
            logger.info(f"[{format_ist_timestamp()}] ✅ Task done: {self.name}")
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[{format_ist_timestamp()}] ❌ Task failed: {self.name} — {e}")


class ContinuousLearner:
    """
    Runs as a background process (or standalone) 24/7.
    Learns from every market day and adapts the strategy permanently.
    """

    def __init__(self):
        self.running  = False
        self.tasks:   List[ScheduledTask] = []
        self._modules: Dict = {}

    # ── INITIALISE ALL MODULES ─────────────────────────────

    def _load_modules(self):
        """Lazy-load all bot modules. Avoids import errors at startup."""
        try:
            from data_fetch_groww import get_data_fetcher
            self._modules["fetcher"] = get_data_fetcher()
        except Exception as e:
            logger.warning(f"Fetcher not available: {e}")

        try:
            from market_data_store import get_data_store
            self._modules["store"] = get_data_store()
        except Exception as e:
            logger.warning(f"Data store not available: {e}")

        try:
            from self_learning import get_learner
            self._modules["learner"] = get_learner()
        except Exception as e:
            logger.warning(f"Learner not available: {e}")

        try:
            from ai_brain import get_ai_brain
            self._modules["ai"] = get_ai_brain()
        except Exception as e:
            logger.warning(f"AI Brain not available: {e}")

        try:
            from trade_journal import get_journal
            self._modules["journal"] = get_journal()
        except Exception as e:
            logger.warning(f"Journal not available: {e}")

        try:
            from overnight_analyzer import get_overnight_analyzer
            self._modules["overnight"] = get_overnight_analyzer()
        except Exception as e:
            logger.warning(f"Overnight analyzer not available: {e}")

        try:
            from economic_calendar import get_calendar
            self._modules["calendar"] = get_calendar()
        except Exception as e:
            logger.warning(f"Calendar not available: {e}")

        try:
            from alerts_telegram import TelegramAlerter
            import config
            if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
                self._modules["alerter"] = TelegramAlerter(
                    config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID
                )
        except Exception as e:
            logger.warning(f"Alerter not available: {e}")

        try:
            from watchlist_manager import WatchlistManager
            self._modules["watchlist"] = WatchlistManager()
        except Exception as e:
            logger.warning(f"Watchlist not available: {e}")

        logger.info(f"[{format_ist_timestamp()}] Modules loaded: {list(self._modules.keys())}")

    # ── REGISTER ALL SCHEDULED TASKS ──────────────────────

    def _register_tasks(self):
        """Register all scheduled learning tasks."""

        # EOD data download (Mon-Fri, after market close)
        self.tasks.append(ScheduledTask(
            "EOD Data Download", 15, 45,
            self._task_eod_data_download,
            weekdays=[0,1,2,3,4]
        ))

        # Self-learning cycle (Mon-Fri)
        self.tasks.append(ScheduledTask(
            "Self-Learning Cycle", 16, 0,
            self._task_self_learning,
            weekdays=[0,1,2,3,4]
        ))

        # AI EOD trade review (Mon-Fri)
        self.tasks.append(ScheduledTask(
            "AI EOD Trade Review", 16, 30,
            self._task_ai_eod_review,
            weekdays=[0,1,2,3,4]
        ))

        # Walk-forward trainer (Mon-Fri)
        self.tasks.append(ScheduledTask(
            "Incremental Trainer", 17, 0,
            self._task_incremental_train,
            weekdays=[0,1,2,3,4]
        ))

        # Weekly deep review (Sunday evening)
        self.tasks.append(ScheduledTask(
            "Weekly Strategy Review", 19, 0,
            self._task_weekly_review,
            weekdays=[6]   # Sunday
        ))

        # Economic calendar refresh (daily)
        self.tasks.append(ScheduledTask(
            "Calendar Refresh", 20, 0,
            self._task_calendar_refresh
        ))

        # US market snapshot (US markets open ~9:30 PM IST)
        self.tasks.append(ScheduledTask(
            "US Market Snapshot", 22, 0,
            self._task_us_snapshot
        ))

        # Overnight analysis — after US markets close (~2 AM IST)
        self.tasks.append(ScheduledTask(
            "Overnight Analysis", 1, 30,
            self._task_overnight_analysis
        ))

        # Asian markets + Gift Nifty fetch (Mon-Fri)
        self.tasks.append(ScheduledTask(
            "Asian Markets Fetch", 6, 30,
            self._task_asian_markets,
            weekdays=[0,1,2,3,4]
        ))

        # Pre-market intelligence (Mon-Fri)
        self.tasks.append(ScheduledTask(
            "Pre-Market Intelligence", 8, 0,
            self._task_premarket_intel,
            weekdays=[0,1,2,3,4]
        ))

        # Morning brief to Telegram (Mon-Fri)
        self.tasks.append(ScheduledTask(
            "Morning Brief", 8, 30,
            self._task_morning_brief,
            weekdays=[0,1,2,3,4]
        ))

        logger.info(f"[{format_ist_timestamp()}] {len(self.tasks)} tasks scheduled")

    # ── TASK IMPLEMENTATIONS ───────────────────────────────

    def _task_eod_data_download(self):
        """Download fresh candle data for all watchlist stocks after market close."""
        store   = self._modules.get("store")
        fetcher = self._modules.get("fetcher")
        wl_mgr  = self._modules.get("watchlist")
        if not store or not fetcher:
            return

        import config
        symbols = (
            wl_mgr.get_watchlist() if wl_mgr
            else config.DEFAULT_WATCHLIST
        )
        logger.info(f"[{format_ist_timestamp()}] Downloading data for {len(symbols)} symbols...")
        result = store.download_all(symbols, fetcher, intervals=["5m","15m","1h","1d"])
        logger.info(f"[{format_ist_timestamp()}] {store.get_store_summary()}")

        # Also update market calendar with today's Nifty data
        try:
            nifty_q = fetcher.get_nifty_quote()
            if nifty_q:
                from economic_calendar import get_calendar
                cal = get_calendar()
                today_events = [ev["event"] for ev in cal.get_today_events()
                                if ev["impact"] != "HOLIDAY"]
                store.log_market_day(
                    nifty_open  = float(nifty_q.get("open", 0)),
                    nifty_close = float(nifty_q.get("ltp",  0)),
                    events      = today_events,
                )
        except Exception as e:
            logger.warning(f"Market calendar update failed: {e}")

    def _task_self_learning(self):
        """Run adaptive self-learning on today's trades."""
        learner = self._modules.get("learner")
        if not learner:
            return
        updated_cfg = learner.run_learning_cycle(lookback_days=20)
        logger.info(f"[{format_ist_timestamp()}] {learner.get_learning_summary()}")

        # Send learning report to Telegram
        alerter = self._modules.get("alerter")
        if alerter:
            try:
                import asyncio
                asyncio.run(alerter.send_text(learner.get_learning_summary()))
            except Exception:
                pass

    def _task_ai_eod_review(self):
        """Ask AI to review today's trades and extract lessons."""
        ai      = self._modules.get("ai")
        journal = self._modules.get("journal")
        if not ai or not journal:
            return

        today_trades = journal.get_today_trades()
        if not today_trades:
            logger.info(f"[{format_ist_timestamp()}] No trades today — skipping AI review")
            return

        summary  = journal.get_today_summary()
        daily_pnl = summary.get("net_pnl", 0)

        trade_dicts = []
        for t in today_trades:
            trade_dicts.append({
                "symbol":        t.symbol,
                "direction":     t.direction,
                "entry_pattern": t.entry_pattern,
                "signal_score":  t.signal_score,
                "net_pnl":       t.net_pnl,
                "outcome":       t.outcome,
                "exit_reason":   t.exit_reason,
                "session":       t.session,
                "regime":        t.regime,
            })

        analysis = ai.analyse_day_trades(
            trades          = trade_dicts,
            daily_pnl       = daily_pnl,
            market_context  = f"Date: {get_current_ist_date()}"
        )

        # Apply pattern feedback from AI to self-learner
        learner = self._modules.get("learner")
        if learner and analysis.get("pattern_feedback"):
            for pattern, score in analysis["pattern_feedback"].items():
                # AI score 0-100 maps to weight 0-1.5
                weight = max(0.0, min(score / 100 * 1.5, 1.5))
                if weight < 0.3 and score < 30:
                    weight = 0.0   # Disable if AI rates very low
                learner.config.pattern_weights[pattern] = round(weight, 2)
            learner._save_config()

        # Log key lesson to Telegram
        alerter = self._modules.get("alerter")
        if alerter and analysis.get("lessons"):
            lesson_text = (
                f"🧠 AI EOD Lesson — {get_current_ist_date()}\n"
                + "\n".join(f"• {l}" for l in analysis["lessons"][:3])
                + f"\n\n📌 Tomorrow: {analysis.get('tomorrow_focus','')}"
            )
            try:
                import asyncio
                asyncio.run(alerter.send_text(lesson_text))
            except Exception:
                pass

    def _task_incremental_train(self):
        """Run a quick incremental backtest on the latest data."""
        store   = self._modules.get("store")
        learner = self._modules.get("learner")
        if not store:
            return

        import config
        symbols = config.DEFAULT_WATCHLIST[:5]  # Quick train on top 5 stocks

        results = []
        for symbol in symbols:
            try:
                df = store.load_candles(symbol, "5m", days=30)
                if df is None or len(df) < 100:
                    continue
                # Add indicators
                try:
                    from pattern_recognition import TechnicalIndicators
                    df = TechnicalIndicators().compute(df)
                except Exception:
                    continue
                from trainer import BacktestEngine, WalkForwardOptimizer
                signals = WalkForwardOptimizer(df)._generate_signals(df, 1.5)
                if signals.empty:
                    continue
                engine = BacktestEngine(100000)
                stats  = engine.run(df, signals)
                if "win_rate" in stats:
                    results.append((symbol, stats))
                    logger.info(
                        f"[{format_ist_timestamp()}] Quick backtest {symbol}: "
                        f"WR={stats['win_rate']:.1f}% "
                        f"Sharpe={stats['sharpe']:.2f}"
                    )
            except Exception as e:
                logger.debug(f"Incremental train failed {symbol}: {e}")

        if results:
            avg_wr = sum(r[1]["win_rate"] for r in results) / len(results)
            logger.info(
                f"[{format_ist_timestamp()}] Incremental train: "
                f"avg WR={avg_wr:.1f}% across {len(results)} symbols"
            )

    def _task_weekly_review(self):
        """Full weekly strategy review using AI (Sundays)."""
        ai      = self._modules.get("ai")
        journal = self._modules.get("journal")
        if not ai or not journal:
            return

        stats_df = journal.get_trades_last_n_days(7)
        if stats_df.empty:
            logger.info("No trades this week for review")
            return

        wins   = stats_df[stats_df["outcome"] == "WIN"]
        losses = stats_df[stats_df["outcome"] == "LOSS"]
        wr     = len(wins) / max(len(stats_df), 1) * 100
        net    = stats_df["net_pnl"].sum() if "net_pnl" in stats_df else 0

        weekly_stats = {
            "total_trades": len(stats_df),
            "win_rate":     round(wr, 1),
            "net_pnl":      round(net, 2),
            "sharpe":       0,
            "max_drawdown": 0,
        }

        pat_stats = journal.get_best_patterns(top_n=10, min_trades=3)
        review    = ai.weekly_strategy_review(weekly_stats, pat_stats)

        logger.info(f"[{format_ist_timestamp()}] Weekly Review:\n{review[:500]}")

        alerter = self._modules.get("alerter")
        if alerter:
            try:
                import asyncio
                asyncio.run(alerter.send_text(f"📊 Weekly Review\n{review[:1000]}"))
            except Exception:
                pass

    def _task_calendar_refresh(self):
        """Refresh economic calendar from web."""
        cal = self._modules.get("calendar")
        if cal:
            cal.refresh_from_web()
        upcoming = (self._modules.get("calendar") or
                    __import__("economic_calendar", fromlist=["get_calendar"]).get_calendar())
        logger.info(f"[{format_ist_timestamp()}] {upcoming.format_upcoming_events()}")

    def _task_us_snapshot(self):
        """Log US market opening direction (9:30 PM IST = 9:00 AM ET)."""
        try:
            import yfinance as yf
            spy = yf.download("SPY", period="1d", interval="5m", progress=False, auto_adjust=True)
            if not spy.empty:
                latest = float(spy["Close"].iloc[-1])
                first  = float(spy["Close"].iloc[0])
                chg    = (latest - first) / first * 100
                logger.info(
                    f"[{format_ist_timestamp()}] US snapshot: "
                    f"SPY {chg:+.2f}% intraday"
                )
        except Exception as e:
            logger.debug(f"US snapshot failed: {e}")

    def _task_overnight_analysis(self):
        """Full overnight analysis after US markets close."""
        overnight = self._modules.get("overnight")
        ai        = self._modules.get("ai")
        if overnight:
            result = overnight.run(ai_brain=ai)
            logger.info(
                f"[{format_ist_timestamp()}] Overnight done: "
                f"bias={result.get('day_bias','?')} "
                f"score={result.get('bias_score',0):+d}"
            )

    def _task_asian_markets(self):
        """Refresh Asian market data (runs at 6:30 AM IST after Asian open)."""
        overnight = self._modules.get("overnight")
        if overnight:
            overnight._fetch_gift_nifty()
            logger.info(f"[{format_ist_timestamp()}] Asian markets fetched")

    def _task_premarket_intel(self):
        """Full pre-market intelligence combining all overnight data."""
        overnight = self._modules.get("overnight")
        ai        = self._modules.get("ai")
        if not overnight:
            return
        # Re-run to get latest Gift Nifty (if not run overnight)
        if not overnight._today_analysis:
            overnight.run(ai_brain=ai)
        logger.info(
            f"[{format_ist_timestamp()}] Pre-market intel ready: "
            f"bias={overnight.get_day_bias()} "
            f"gap={overnight.get_gap_pct():+.2f}%"
        )

    def _task_morning_brief(self):
        """Send morning brief to Telegram with full day plan."""
        overnight = self._modules.get("overnight")
        alerter   = self._modules.get("alerter")
        calendar  = self._modules.get("calendar")

        if not overnight or not alerter:
            return

        brief = overnight.format_morning_brief()

        if calendar:
            events_text = calendar.format_upcoming_events()
            brief      += f"\n\n{events_text}"

        # Check for today's high-impact events
        if calendar:
            today_events = calendar.get_today_events()
            high_impact  = [e for e in today_events if e["impact"] in ("HIGH", "EXTREME")]
            if high_impact:
                brief += (
                    f"\n\n🚨 HIGH IMPACT EVENTS TODAY:\n"
                    + "\n".join(f"  • {e['event']} @ {e['time_ist']} IST" for e in high_impact)
                )
                brief += "\n⚠️ REDUCE POSITION SIZES 50% near these events!"

        try:
            import asyncio
            asyncio.run(alerter.send_text(brief))
        except Exception as e:
            logger.warning(f"Morning brief send failed: {e}")

        logger.info(f"[{format_ist_timestamp()}] Morning brief sent")

    # ── MAIN SCHEDULER LOOP ────────────────────────────────

    def start(self, blocking: bool = True):
        """
        Start the continuous learning scheduler.
        blocking=True: runs in foreground (standalone mode)
        blocking=False: runs in background thread
        """
        self._load_modules()
        self._register_tasks()
        self.running = True

        logger.info(
            f"[{format_ist_timestamp()}] Continuous Learner started. "
            f"{len(self.tasks)} tasks registered. "
            f"Checking every 60s."
        )

        if not blocking:
            t = threading.Thread(target=self._loop, daemon=True, name="ContinuousLearner")
            t.start()
            return t

        try:
            self._loop()
        except KeyboardInterrupt:
            logger.info(f"[{format_ist_timestamp()}] Continuous Learner stopped")
        finally:
            self.running = False

    def _loop(self):
        """Main scheduler loop — checks tasks every 60 seconds."""
        while self.running:
            now_ist = get_current_ist_time()
            for task in self.tasks:
                if task.is_due(now_ist):
                    task.run()
            time.sleep(60)

    def stop(self):
        self.running = False

    def get_status(self) -> str:
        lines = [f"🤖 Continuous Learner Status — {format_ist_timestamp()}"]
        for task in self.tasks:
            status = f"Last: {task.last_run_date or 'never'} | Runs: {task.run_count}"
            if task.last_error:
                status += f" | ⚠️ {task.last_error[:40]}"
            lines.append(f"  • {task.name:30s} {status}")
        return "\n".join(lines)

    def run_task_now(self, task_name: str) -> bool:
        """Manually trigger a specific task by name."""
        for task in self.tasks:
            if task.name.lower() == task_name.lower():
                task.run()
                return True
        return False


# ── STANDALONE ENTRY POINT ─────────────────────────────────────────────────

def main():
    import config
    setup_logging(config.LOG_DIR, config.LOG_LEVEL)
    logger.info("=" * 55)
    logger.info("  NSE BOT — Continuous Learner (24/7 Mode)")
    logger.info(f"  IST: {format_ist_timestamp()}")
    logger.info("=" * 55)

    learner = ContinuousLearner()

    def handle_sig(sig, _frame):
        logger.info(f"Signal {sig} — shutting down learner")
        learner.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sig)
    signal.signal(signal.SIGINT,  handle_sig)

    learner.start(blocking=True)


if __name__ == "__main__":
    main()
