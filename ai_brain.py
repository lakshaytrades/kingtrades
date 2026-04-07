"""
ai_brain.py — NSE Momentum Groww AI Bot
Central AI Intelligence Engine — Powered by Claude API

This is the "thinking" layer of the bot. It uses Claude AI to:
1. Analyse market patterns and generate human-like trading insight
2. Review each day's trades and extract lessons
3. Synthesise news + price action + indicators into a daily market thesis
4. Suggest strategy improvements based on changing market conditions
5. Explain EVERY signal in plain English (why this trade, why now)
6. Learn from 18 years of encoded trading rules + live performance data

18yr Rule: "The best traders THINK before they trade.
They ask: Why is price HERE? Who is buying/selling? What does it MEAN?"
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from utils import format_ist_timestamp, get_current_ist_time, get_current_ist_date

logger = logging.getLogger(__name__)

AI_INSIGHTS_DIR = Path("logs/ai_insights")
AI_INSIGHTS_DIR.mkdir(parents=True, exist_ok=True)

# 18 years of trading wisdom — the permanent knowledge base
TRADING_WISDOM = """
You are an expert NSE intraday momentum trader with 18+ years of experience since 2008.
You have traded through: 2008 financial crisis, 2009 recovery, 2011 correction,
2016 demonetization, 2020 COVID crash and recovery, 2022 bear market, 2024 bull run.

Your core principles (never compromise these):
1. CAPITAL PRESERVATION FIRST. A 10% loss needs 11% gain to recover. A 50% loss needs 100%.
2. Trade WITH the trend. The trend is your best friend. Never fight it.
3. Volume confirms everything. Price without volume is a lie.
4. VWAP is the institutional anchor. Above = bullish bias. Below = bearish bias.
5. The opening 45 minutes (9:15-10:00 AM IST) has 40% of daily volume and best momentum.
6. NEVER trade during 11 AM - 1 PM IST. This is the chop zone. Professionals take lunch.
7. After 3 consecutive losses, stop trading and review. The market is telling you something.
8. Position sizing is more important than entry price. A great entry with 10% size = mediocre return.
9. Take partial profits at T1 (50%). Let winners run with trailing stops.
10. Every losing trade is a tuition fee. Learn from it. Don't repeat the same mistake.

NSE-specific knowledge:
- FII (Foreign Institutional Investors) dominate price direction. Watch their flows daily.
- DIIs (Domestic) often buy on dips. Their buying = support.
- Nifty50 direction sets the tone for 80% of stocks.
- Earnings season (Apr, Jul, Oct, Jan) = higher volatility. Widen stops.
- RBI policy days: DO NOT TRADE 30 min before announcement.
- Budget day: THE most volatile day of the year. Only trade after 12 PM post clarity.
- F&O expiry (last Thursday of month): high manipulation. Avoid if unsure.
- SGX Nifty (now Gift Nifty) pre-market futures indicate opening direction.
- Global cues: US market close, Asian markets open — both matter for NSE opening.
"""


class AIBrain:
    """
    Claude-powered AI brain for market analysis and continuous learning.
    Runs during off-market hours and provides insights before market opens.
    """

    def __init__(self):
        self._client = None
        self._model  = "claude-sonnet-4-6"
        self._enabled = bool(os.getenv("ANTHROPIC_API_KEY"))
        if not self._enabled:
            logger.warning(
                f"[{format_ist_timestamp()}] GEMINI_API_KEY not set — "
                "AI brain running in rule-based mode only"
            )
        else:
            self._init_client()

    def _init_client(self):
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            logger.info(f"[{format_ist_timestamp()}] AI Brain (Claude) initialized")
        except ImportError:
            logger.error("anthropic not installed: pip install anthropic")
        except Exception as e:
            logger.error(f"AI Brain init failed: {e}")

    def _ask(self, prompt: str, max_tokens: int = 1024) -> str:
        """Send a prompt to Claude and get response."""
        if not self._client:
            return "[AI Brain offline — set ANTHROPIC_API_KEY]"
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=TRADING_WISDOM,
                messages=[{"role": "user", "content": prompt}]
            )
            return resp.content[0].text
        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] AI Brain query failed: {e}")
            return f"[AI query failed: {e}]"

    # --------------------------------------------------------
    # DAILY MARKET THESIS (runs at 8:30 AM IST)
    # --------------------------------------------------------

    def generate_morning_thesis(
        self,
        nifty_prev_close: float,
        gift_nifty:       float,
        us_market_change: float,
        asian_markets:    Dict[str, float],
        top_news:         List[str],
        economic_events:  List[str],
    ) -> Dict:
        """
        Generate today's trading thesis before market opens.
        Called at 8:30 AM IST — before the TOTP login.

        Returns: {bias, key_levels, sectors_to_watch, avoid_list, thesis_text}
        """
        gap_pct = ((gift_nifty - nifty_prev_close) / nifty_prev_close * 100) if nifty_prev_close else 0
        asian_summary = ", ".join(f"{k}: {v:+.1f}%" for k, v in asian_markets.items())
        news_text     = "\n".join(f"- {n}" for n in top_news[:5])
        events_text   = "\n".join(f"- {e}" for e in economic_events[:3]) or "None today"

        prompt = f"""
Today is {get_current_ist_date()} (IST). NSE opens in ~45 minutes.

OVERNIGHT DATA:
- Nifty50 prev close: {nifty_prev_close:.0f}
- Gift Nifty futures: {gift_nifty:.0f} (implied gap: {gap_pct:+.1f}%)
- US markets last close: {us_market_change:+.1f}%
- Asian markets: {asian_summary}

TOP NEWS:
{news_text}

ECONOMIC EVENTS TODAY:
{events_text}

Based on your 18 years of NSE trading experience, provide:
1. MARKET BIAS (BULLISH/BEARISH/NEUTRAL) and confidence (0-100)
2. NIFTY KEY LEVELS to watch (support and resistance)
3. TOP 3 SECTORS likely to outperform today
4. TOP 3 SECTORS to avoid today
5. OPENING STRATEGY (what to do in first 15 minutes)
6. ANY RED FLAGS (reasons to trade cautiously today)
7. ONE-LINE THESIS for the day

Format as JSON with keys: bias, confidence, support, resistance,
sectors_buy, sectors_avoid, opening_strategy, red_flags, thesis
"""
        response = self._ask(prompt, max_tokens=800)

        # Try to parse JSON, fall back to text
        try:
            # Extract JSON block if wrapped
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                response = response.split("```")[1].split("```")[0]
            result = json.loads(response.strip())
        except Exception:
            result = {
                "bias":             "NEUTRAL",
                "confidence":       50,
                "thesis":           response[:300],
                "opening_strategy": "Wait for direction after first 15 min",
                "red_flags":        [],
                "sectors_buy":      [],
                "sectors_avoid":    [],
            }

        result["generated_at"] = format_ist_timestamp()
        result["gap_pct"]      = round(gap_pct, 2)
        self._save_insight("morning_thesis", result)
        logger.info(f"[{format_ist_timestamp()}] Morning thesis: {result.get('bias','?')} | {result.get('thesis','')[:100]}")
        return result

    # --------------------------------------------------------
    # SIGNAL EXPLANATION (runs before every trade)
    # --------------------------------------------------------

    def explain_signal(
        self,
        symbol:    str,
        direction: str,
        patterns:  List[str],
        score:     float,
        regime:    str,
        mtf_info:  str,
        rsi:       float,
        vwap_pos:  str,
        volume_ratio: float,
    ) -> str:
        """
        Generate a plain-English explanation of WHY this trade.
        Sent with every Telegram alert so you understand each trade.
        """
        prompt = f"""
A trading signal has been generated. Explain it like a professional trader would:

Symbol: {symbol}
Direction: {direction}
Patterns detected: {', '.join(patterns)}
Signal confidence: {score:.0f}/100
Market regime: {regime}
Multi-timeframe: {mtf_info}
RSI: {rsi:.0f}
Price vs VWAP: {vwap_pos}
Volume ratio: {volume_ratio:.1f}x average

In 3-4 sentences, explain:
1. WHY this is a good setup (the edge)
2. WHAT could go wrong (the risk)
3. ONE key thing to watch during this trade

Be direct and specific. Use the mindset of an 18-year NSE veteran.
"""
        return self._ask(prompt, max_tokens=300)

    # --------------------------------------------------------
    # EOD LEARNING (runs after market close)
    # --------------------------------------------------------

    def analyse_day_trades(self, trades: List[Dict], daily_pnl: float, market_context: str) -> Dict:
        """
        Review the day's trades and extract lessons.
        Called at EOD, results fed into self_learning.py.
        """
        if not trades:
            return {"lessons": [], "pattern_feedback": {}, "summary": "No trades today."}

        trade_summary = "\n".join([
            f"- {t.get('symbol','?')} {t.get('direction','?')}: "
            f"Pattern={t.get('entry_pattern','?')} "
            f"Score={t.get('signal_score',0):.0f} "
            f"PnL=₹{t.get('net_pnl',0):+.0f} "
            f"({t.get('outcome','?')}) "
            f"Exit={t.get('exit_reason','?')}"
            for t in trades[:20]
        ])

        prompt = f"""
Today's NSE intraday trading session is complete.
Date: {get_current_ist_date()} (IST)
Total P&L: ₹{daily_pnl:+.0f}
Market context: {market_context}

TRADES TODAY:
{trade_summary}

As an 18-year NSE veteran, analyse these trades and provide:
1. WHAT WORKED: Which patterns/setups produced profits? Why?
2. WHAT FAILED: Which losses were avoidable? What was the mistake?
3. MARKET LESSON: What did the market teach today?
4. TOMORROW'S EDGE: Based on today, what to focus on tomorrow?
5. PATTERN_SCORES: Rate each pattern that appeared (0-100 confidence for tomorrow)
6. ONE RULE: One specific rule to add/change based on today

Format as JSON: lessons (list), pattern_feedback (dict name->score), 
tomorrow_focus (string), rule_change (string), summary (string)
"""
        response = self._ask(prompt, max_tokens=1000)
        try:
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                response = response.split("```")[1].split("```")[0]
            result = json.loads(response.strip())
        except Exception:
            result = {
                "lessons":         [response[:400]],
                "pattern_feedback": {},
                "tomorrow_focus":   "Review failed signals",
                "rule_change":      "None identified",
                "summary":          response[:200],
            }

        result["date_ist"]     = str(get_current_ist_date())
        result["daily_pnl"]    = daily_pnl
        result["trade_count"]  = len(trades)
        self._save_insight("eod_analysis", result)
        return result

    # --------------------------------------------------------
    # WEEKLY STRATEGY REVIEW (runs every Sunday)
    # --------------------------------------------------------

    def weekly_strategy_review(self, weekly_stats: Dict, pattern_stats: List[Dict]) -> str:
        """
        Deep weekly review of strategy performance.
        Generates actionable improvements for next week.
        """
        pat_text = "\n".join([
            f"  {p['pattern']}: {p['win_rate']:.0f}% WR, {p['trades']} trades, avg ₹{p['avg_pnl']:+.0f}"
            for p in pattern_stats[:10]
        ])

        prompt = f"""
Weekly NSE trading strategy review — Week ending {get_current_ist_date()}

WEEKLY STATS:
- Total trades: {weekly_stats.get('total_trades', 0)}
- Win rate: {weekly_stats.get('win_rate', 0):.1f}%
- Net P&L: ₹{weekly_stats.get('net_pnl', 0):+.0f}
- Sharpe: {weekly_stats.get('sharpe', 0):.2f}
- Max drawdown: {weekly_stats.get('max_drawdown', 0):.1f}%

PATTERN PERFORMANCE:
{pat_text}

As an 18-year NSE veteran, provide a weekly review:
1. STRATEGY HEALTH (is it working? Why/why not?)
2. TOP 3 IMPROVEMENTS for next week
3. PATTERNS TO INCREASE SIZE on (high performers)
4. PATTERNS TO STOP TRADING (chronic losers)
5. MARKET CONDITION ASSESSMENT (trending/ranging? What's next week likely to bring?)
6. RISK ADJUSTMENT (should we increase/decrease position size next week?)

Be specific and actionable. This is real money.
"""
        review = self._ask(prompt, max_tokens=1200)
        self._save_insight("weekly_review", {"review": review, "stats": weekly_stats})
        return review

    # --------------------------------------------------------
    # MARKET PATTERN LEARNING (runs nightly)
    # --------------------------------------------------------

    def learn_new_patterns(self, recent_data_summary: str) -> List[Dict]:
        """
        Ask AI to identify any new patterns or market behaviours from recent data.
        This is how the bot stays up-to-date with evolving market conditions.
        """
        prompt = f"""
Analyse recent NSE market data and identify any patterns or behaviours that
a momentum intraday trader should know about:

RECENT MARKET DATA SUMMARY:
{recent_data_summary}

Identify:
1. Any NEW patterns emerging (not in standard playbooks)
2. TIME-OF-DAY anomalies (e.g., is 2 PM rally happening consistently?)
3. SECTOR ROTATION patterns
4. VOLUME PROFILE changes (is morning or afternoon volume dominant?)
5. Any GLOBAL CORRELATION shifts (is US correlation higher/lower than usual?)

For each finding, rate confidence (0-100) and tradeable impact (HIGH/MEDIUM/LOW).
Format as JSON list of: {{finding, type, confidence, impact, action}}
"""
        response = self._ask(prompt, max_tokens=800)
        try:
            if "```" in response:
                response = response.split("```")[1].split("```")[0]
                if response.startswith("json"):
                    response = response[4:]
            findings = json.loads(response.strip())
            if isinstance(findings, list):
                self._save_insight("pattern_discoveries", {"findings": findings})
                return findings
        except Exception:
            pass
        return []

    # --------------------------------------------------------
    # STOCK-SPECIFIC ANALYSIS
    # --------------------------------------------------------

    def analyse_stock(self, symbol: str, technicals: Dict, news: List[str], sector_trend: str) -> str:
        """Quick AI assessment of a stock before adding to watchlist."""
        news_text = "\n".join(f"- {n}" for n in news[:3]) or "No recent news"
        prompt = f"""
Quick pre-trade assessment for NSE intraday trading:

Stock: {symbol}
Sector trend: {sector_trend}
Technical snapshot:
- RSI: {technicals.get('rsi', 50):.0f}
- MACD: {'bullish' if technicals.get('macd_hist', 0) > 0 else 'bearish'}
- Volume ratio: {technicals.get('volume_ratio', 1):.1f}x
- vs VWAP: {'above' if technicals.get('above_vwap', False) else 'below'}
- ATR%: {technicals.get('atr_pct', 0.3):.2f}%

Recent news:
{news_text}

In 2 sentences: Is this stock worth watching today for momentum trading? 
Flag any red flags. Be direct.
"""
        return self._ask(prompt, max_tokens=150)

    # --------------------------------------------------------
    # PERSISTENCE
    # --------------------------------------------------------

    def _save_insight(self, category: str, data: Dict):
        """Save AI insight to disk for audit and future learning."""
        today = get_current_ist_date()
        path  = AI_INSIGHTS_DIR / f"{category}_{today}.json"
        try:
            existing = []
            if path.exists():
                with open(path) as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = [existing]
            existing.append(data)
            with open(path, "w") as f:
                json.dump(existing, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Could not save insight: {e}")

    def get_today_thesis(self) -> Optional[Dict]:
        """Load today's morning thesis if it exists."""
        today = get_current_ist_date()
        path  = AI_INSIGHTS_DIR / f"morning_thesis_{today}.json"
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                return data[-1] if isinstance(data, list) else data
            except Exception:
                pass
        return None


# Singleton
_brain: Optional["AIBrain"] = None

def get_ai_brain() -> "AIBrain":
    global _brain
    if _brain is None:
        _brain = AIBrain()
    return _brain
