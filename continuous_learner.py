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
        self._token_refreshed_date: str = ""  # YYYY-MM-DD — skip cascade once refreshed

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

        # EOD Self-Training (Mon-Fri at 4:30 PM IST)
        # Runs after market close, optimizes tomorrow's params from last 30 days
        self.tasks.append(ScheduledTask(
            "EOD Self-Training", 16, 30,
            self._task_eod_self_training,
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

        # ── Groww Cloud token refresh cascade (keys reset at 6 AM IST) ──────
        # Try at 6:05, 6:20, 6:40, 7:00, 8:00 — stop as soon as one succeeds
        for h, m in [(6, 5), (6, 20), (6, 40), (7, 0), (8, 0)]:
            self.tasks.append(ScheduledTask(
                f"Token Refresh {h:02d}:{m:02d}",
                h, m,
                self._task_token_refresh,
                weekdays=[0, 1, 2, 3, 4],
                run_on_holidays=True,   # Refresh even on holidays — token still expires
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
                alerter.send_text(learner.get_learning_summary())
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
                alerter.send_text(lesson_text)
            except Exception:
                pass

    def _task_eod_self_training(self):
        """
        EOD Self-Training: runs after market close at 4:30 PM IST Mon-Fri.

        Uses last 30 days of 5m data to walk-forward optimize ATR SL/TP,
        risk%, and minimum signal score. Saves to data/adaptive_params.json.
        Main.py reads this file every morning at 9:00 AM IST and applies
        the updated parameters for that day's live trading.
        """
        fetcher = self._modules.get("fetcher")
        alerter = self._modules.get("alerter")
        if not fetcher:
            logger.warning(f"[{format_ist_timestamp()}] EOD Self-Train: no fetcher available")
            return

        import config
        try:
            from trainer import EODSelfTrainer
            trainer = EODSelfTrainer()
            symbols = config.DEFAULT_WATCHLIST[:10]
            result  = trainer.run(
                fetcher   = fetcher,
                symbols   = symbols,
                lookback  = 30,
                n_trials  = 20,   # Fast but meaningful (Render CPU-safe)
                n_splits  = 3,
                alerter   = alerter,
            )
            logger.info(
                f"[{format_ist_timestamp()}] EOD Self-Training done: "
                f"improved={result.get('improved')} | "
                f"Sharpe={result.get('sharpe',0):.2f} | "
                f"WR={result.get('win_rate',0):.1f}%"
            )
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] EOD Self-Training failed: {e}")

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
                alerter.send_text(f"📊 Weekly Review\n{review[:1000]}")
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
            alerter.send_text(brief)
        except Exception as e:
            logger.warning(f"Morning brief send failed: {e}")

        logger.info(f"[{format_ist_timestamp()}] Morning brief sent")

    def _task_token_refresh(self):
        """
        Groww Cloud token refresh cascade — runs at 6:05, 6:20, 6:40, 7:00, 8:00 AM IST.
        Groww Cloud API keys reset at 6 AM IST daily.
        As soon as one attempt succeeds, all later cascade attempts are skipped.
        """
        today = str(get_current_ist_date())
        if self._token_refreshed_date == today:
            logger.info(f"[{format_ist_timestamp()}] Token already refreshed today — cascade skip")
            return

        alerter = self._modules.get("alerter")

        try:
            from auth_groww import get_auth_manager
            mgr = get_auth_manager()
            old_token = mgr.token or ""

            logger.info(f"[{format_ist_timestamp()}] Token refresh cascade attempt...")
            success = mgr.refresh_token_if_needed()
            new_token = mgr.token or ""

            if success and new_token and new_token != old_token:
                # Token genuinely changed — propagate to fetcher + executor
                self._token_refreshed_date = today
                logger.info(f"[{format_ist_timestamp()}] ✅ Groww token refreshed (cascade success)")

                # Kick the data-fetcher singleton so it picks up the new token
                try:
                    from data_fetch_groww import get_data_fetcher
                    fetcher = get_data_fetcher()
                    if hasattr(fetcher, "_refresh_api_if_needed"):
                        fetcher._refresh_api_if_needed()
                except Exception as e:
                    logger.debug(f"Fetcher token propagation: {e}")

                if alerter:
                    try:
                        alerter.send_html(
                            f"🔑 <b>Groww Token Refreshed</b> — {format_ist_timestamp()}\n"
                            f"Ready for today's trading session."
                        )
                    except Exception:
                        pass

            elif success and new_token == old_token:
                # Same token returned — may be using cached env token; still mark success
                self._token_refreshed_date = today
                logger.info(
                    f"[{format_ist_timestamp()}] Token refresh returned same token "
                    f"(env fallback). Cascade will not retry."
                )
            else:
                logger.warning(
                    f"[{format_ist_timestamp()}] Token refresh attempt failed — "
                    f"next cascade attempt will retry."
                )
                if alerter:
                    try:
                        alerter.send_html(
                            f"⚠️ <b>Token Refresh Failed</b> — {format_ist_timestamp()}\n"
                            f"Will retry. Check GROWW_TOTP_SECRET in Render env."
                        )
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Token refresh task error: {e}")

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
