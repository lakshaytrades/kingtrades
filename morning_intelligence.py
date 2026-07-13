"""
morning_intelligence.py — NSE Momentum Groww AI Bot
Smart Morning Briefing Engine

Aggregates ALL data sources into a structured market thesis for the day.
Sent to Telegram at 8:30 AM IST. Zero AI API needed — pure logic.

Answers every morning:
  - Which direction is the market likely to go today?
  - Which sectors are hot — where should capital go?
  - What are the top 3-5 stocks to watch?
  - What risks exist today (RBI, earnings, global)?
  - What's the expected Nifty range?
  - How aggressive should the bot be? (AGGRESSIVE / NORMAL / CAUTIOUS / DEFENSIVE)

Data sources aggregated:
  1. fii_dii_tracker.py  — FIIDIITracker: FII net buy/sell, 5-day trend
  2. option_chain.py     — OptionChainAnalyzer: PCR, max pain, OI bias
  3. sector_rotation.py  — SectorRotationEngine: hot/cold sectors
  4. overnight_analyzer.py — OvernightAnalyzer: global cues, Gift Nifty, VIX
  5. gap_analyzer.py     — GapAnalyzer: pre-market gap stocks
  6. economic_calendar.py — EconomicCalendar: today's events
  7. news_filter.py      — NewsFilter: market-level news sentiment
  8. rl_agent.py         — LakshKingRL.status_message(): RL brain status

⚠️ WARNING: This bot places REAL orders with REAL money on Groww.
Server runs in UK (UTC) — all timestamps in IST (Asia/Kolkata).
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from utils import format_ist_timestamp, get_current_ist_date, get_current_ist_time

logger = logging.getLogger(__name__)


# ============================================================
# DATA CLASS
# ============================================================

@dataclass
class DayThesis:
    """Complete market thesis for the trading day."""
    date:             str            # "2026-05-07"
    market_bias:      str            # "BULLISH", "BEARISH", "NEUTRAL"
    bias_score:       int            # -100 to +100
    trading_mode:     str            # "AGGRESSIVE", "NORMAL", "CAUTIOUS", "DEFENSIVE"
    size_multiplier:  float          # 1.2=AGGRESSIVE, 1.0=NORMAL, 0.7=CAUTIOUS, 0.5=DEFENSIVE
    spy_range_low:    float          # Expected SPY low (VIX-based + gap adj)
    spy_range_high:   float          # Expected SPY high
    hot_sectors:      List[str]      # Top 3 sectors to trade
    avoid_sectors:    List[str]      # Bottom 2 sectors to avoid
    top_watchlist:    List[str]      # Top 5 stocks
    key_risks:        List[str]      # e.g. ["Fed decision at 2PM ET"]
    fii_bias:         str            # "BUYING", "SELLING", "NEUTRAL" (institutional flow)
    oc_bias:          str            # "BULLISH", "BEARISH", "NEUTRAL"
    vix:              float
    spy_gap_change:   float          # SPY pre-market gap %
    summary:          str            # 2-sentence human-readable summary
    rl_status:        str = ""       # RL brain status line (optional)


# ============================================================
# TRADING MODE CONSTANTS
# ============================================================

TRADING_MODES = {
    "AGGRESSIVE": 1.2,
    "NORMAL":     1.0,
    "CAUTIOUS":   0.7,
    "DEFENSIVE":  0.5,
}

BIAS_SCORE_THRESHOLDS = {
    "BULLISH": 30,
    "BEARISH": -30,
}

# Bias scoring weights
SCORE_FII_STRONG_BUY   = +20   # FII net > 1000 Cr
SCORE_FII_STRONG_SELL  = -20   # FII net < -1000 Cr
SCORE_OC_BULLISH       = +15
SCORE_OC_BEARISH       = -15
SCORE_SPY_GAP_UP       = +15   # spy_gap_change > +0.5%
SCORE_SPY_GAP_DOWN     = -15   # spy_gap_change < -0.5%
SCORE_VIX_LOW          = +10   # VIX < 14
SCORE_VIX_HIGH         = -10   # VIX > 22
SCORE_SECTOR_HOT       = +10   # at least one HOT sector
SCORE_EVENT_PENALTY    = -10   # major event today (uncertainty)


# ============================================================
# MORNING INTELLIGENCE ENGINE
# ============================================================

class MorningIntelligence:
    """
    Aggregates all pre-market data sources into a single structured day thesis.
    The thesis drives trading mode, position sizing, and watchlist priority
    for the entire trading session.

    All exceptions per data source are caught — the morning brief is ALWAYS
    delivered even if some sources fail.
    """

    def __init__(
        self,
        fii_tracker=None,
        oc_analyzer=None,
        sector_rotation=None,
        overnight=None,
        gap_analyzer=None,
        calendar=None,
        news_filter=None,
    ):
        self._fii     = fii_tracker
        self._oc      = oc_analyzer
        self._sectors = sector_rotation
        self._overnight = overnight
        self._gap     = gap_analyzer
        self._calendar = calendar
        self._news    = news_filter

    # ──────────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────────

    def generate(self, watchlist: List[str] = None) -> DayThesis:
        """
        Main method — runs all analysis, returns DayThesis.
        Each data source is wrapped in try/except so one failure
        never blocks the morning brief.
        """
        today = str(get_current_ist_date())
        watchlist = watchlist or []

        logger.info(f"[{format_ist_timestamp()}] MorningIntelligence.generate() starting for {today}")

        # ── 1. FII scoring ────────────────────────────────────
        fii_score, fii_bias, fii_net = self._score_fii()
        logger.info(f"[{format_ist_timestamp()}] FII score={fii_score:+d} bias={fii_bias} net={fii_net:+.0f}Cr")

        # ── 2. Option chain scoring ───────────────────────────
        oc_score, oc_bias, pcr = self._score_oc()
        logger.info(f"[{format_ist_timestamp()}] OC score={oc_score:+d} bias={oc_bias} PCR={pcr:.2f}")

        # ── 3. Overnight / global cues ────────────────────────
        overnight_score, vix, spy_gap_change, spy_prev = self._score_overnight()
        logger.info(
            f"[{format_ist_timestamp()}] Overnight score={overnight_score:+d} "
            f"VIX={vix:.1f} SPY_gap={spy_gap_change:+.2f}% SPYPrev=${spy_prev:.2f}"
        )

        # ── 4. Events / risk ──────────────────────────────────
        key_risks, event_severity = self._get_events_risk()
        logger.info(
            f"[{format_ist_timestamp()}] Events severity={event_severity} risks={len(key_risks)}"
        )

        # ── 5. Sector rotation ────────────────────────────────
        hot_sectors, avoid_sectors, sector_score = self._get_sectors()
        logger.info(
            f"[{format_ist_timestamp()}] Hot sectors={hot_sectors} avoid={avoid_sectors}"
        )

        # ── 6. Top watchlist ──────────────────────────────────
        top_watchlist = self._build_top_watchlist(watchlist, hot_sectors)
        logger.info(f"[{format_ist_timestamp()}] Top watchlist={top_watchlist}")

        # ── 7. Composite bias score ───────────────────────────
        bias_score = self._calc_bias_score(
            fii_score, oc_score, overnight_score, sector_score, event_severity
        )
        # Clamp to -100..+100
        bias_score = max(-100, min(100, bias_score))

        if bias_score >= BIAS_SCORE_THRESHOLDS["BULLISH"]:
            market_bias = "BULLISH"
        elif bias_score <= BIAS_SCORE_THRESHOLDS["BEARISH"]:
            market_bias = "BEARISH"
        else:
            market_bias = "NEUTRAL"

        # ── 8. Trading mode ───────────────────────────────────
        trading_mode, size_multiplier = self._determine_mode(bias_score, vix, event_severity)
        logger.info(
            f"[{format_ist_timestamp()}] Mode={trading_mode} size={size_multiplier}x "
            f"bias={market_bias}({bias_score:+d})"
        )

        # ── 9. Nifty range ────────────────────────────────────
        spy_low, spy_high = self._get_nifty_range(spy_prev, vix, spy_gap_change)

        # ── 10. RL brain status ───────────────────────────────
        rl_status = self._get_rl_status()

        # ── 11. Build thesis ──────────────────────────────────
        thesis = DayThesis(
            date=today,
            market_bias=market_bias,
            bias_score=bias_score,
            trading_mode=trading_mode,
            size_multiplier=size_multiplier,
            spy_range_low=round(spy_low, 2),
            spy_range_high=round(spy_high, 2),
            hot_sectors=hot_sectors,
            avoid_sectors=avoid_sectors,
            top_watchlist=top_watchlist,
            key_risks=key_risks,
            fii_bias=fii_bias,
            oc_bias=oc_bias,
            vix=round(vix, 1),
            spy_gap_change=round(spy_gap_change, 2),
            summary="",
            rl_status=rl_status,
        )
        thesis.summary = self._build_summary(thesis, fii_net, pcr)

        logger.info(
            f"[{format_ist_timestamp()}] DayThesis complete: "
            f"{market_bias}({bias_score:+d}) {trading_mode} "
            f"SPY=${spy_low:.2f}–${spy_high:.2f} "
            f"VIX={vix:.1f}"
        )
        return thesis

    def format_telegram(self, thesis: DayThesis) -> str:
        """
        Rich Telegram HTML message for morning brief.
        Follows the specified format with section dividers.
        """
        ist_time = get_current_ist_time()
        # e.g. "Thu 07 May 2026"
        day_str = ist_time.strftime("%a %d %b %Y")

        bias_emoji = {
            "BULLISH": "🟢",
            "BEARISH": "🔴",
            "NEUTRAL": "🟡",
        }.get(thesis.market_bias, "🟡")

        mode_emoji = {
            "AGGRESSIVE": "⚡",
            "NORMAL":     "✅",
            "CAUTIOUS":   "⚠️",
            "DEFENSIVE":  "🛡️",
        }.get(thesis.trading_mode, "✅")

        fii_emoji = "🟢" if thesis.fii_bias == "BUYING" else ("🔴" if thesis.fii_bias == "SELLING" else "🟡")
        oc_emoji  = "🟢" if thesis.oc_bias  == "BULLISH" else ("🔴" if thesis.oc_bias  == "BEARISH" else "🟡")
        gap_sign = "+" if thesis.spy_gap_change >= 0 else ""

        # Format sectors
        hot_str   = "  " + "  ".join(
            f"{i+1}. {s}" for i, s in enumerate(thesis.hot_sectors[:3])
        ) if thesis.hot_sectors else "  None identified"
        avoid_str = "  " + ", ".join(thesis.avoid_sectors[:2]) if thesis.avoid_sectors else "  None"

        # Format watchlist
        wl_str = "  " + ", ".join(thesis.top_watchlist[:5]) if thesis.top_watchlist else "  None"

        # Format risks
        if thesis.key_risks:
            risks_str = "\n".join(f"  • {r}" for r in thesis.key_risks[:5])
        else:
            risks_str = "  • No major events today"

        # RL brain (compact summary)
        rl_line = ""
        if thesis.rl_status:
            # Extract key stats from the status message
            try:
                lines = [l.strip() for l in thesis.rl_status.split("\n") if l.strip()]
                # Find states, win rate, epsilon lines
                states_line = next((l for l in lines if "States" in l or "states" in l), "")
                wr_line     = next((l for l in lines if "Win rate" in l or "win rate" in l), "")
                ep_line     = next((l for l in lines if "Exploration" in l or "epsilon" in l.lower()), "")

                # Parse values
                states_val = "?"
                wr_val     = "?"
                ep_val     = "?"
                if ":" in states_line:
                    states_val = states_line.split(":")[-1].strip()
                if ":" in wr_line:
                    wr_val = wr_line.split(":")[-1].strip().split("%")[0].strip() + "%"
                # Exploration (ε): 30.0% → ...  parse the first % token after the last ":"
                if ":" in ep_line:
                    raw_ep = ep_line.split(":")[-1].strip()   # "30.0% → still learning"
                    pct_token = raw_ep.split("%")[0].strip()  # "30.0"
                    try:
                        ep_val = f"{float(pct_token):.0f}%"
                    except Exception:
                        ep_val = pct_token if pct_token else "?"

                rl_line = f"\n🧠 RL Brain: {states_val} states | WR: {wr_val} | ε: {ep_val}"
            except Exception:
                rl_line = f"\n🧠 RL Brain: active"

        sep = "━" * 28

        msg = (
            f"🌅 <b>MORNING INTELLIGENCE — {day_str}</b>\n"
            f"{sep}\n"
            f"📊 Market Bias: {bias_emoji} <b>{thesis.market_bias}</b> (score: {thesis.bias_score:+d})\n"
            f"{mode_emoji} Mode: <b>{thesis.trading_mode}</b> ({thesis.size_multiplier}x size)\n"
            f"📈 SPY Range: <b>${thesis.spy_range_low:,.2f} – ${thesis.spy_range_high:,.2f}</b>"
            f" (VIX: {thesis.vix})\n"
            f"\n"
            f"💰 Inst. Flow: {fii_emoji} <b>{thesis.fii_bias}</b>\n"
            f"🔗 Options Flow: {oc_emoji} <b>{thesis.oc_bias}</b>\n"
            f"🌍 SPY Gap: <b>{gap_sign}{thesis.spy_gap_change:.2f}%</b>\n"
            f"\n"
            f"🔥 <b>Hot Sectors (trade these):</b>\n{hot_str}\n"
            f"\n"
            f"❄️ <b>Avoid Today:</b>\n{avoid_str}\n"
            f"\n"
            f"👀 <b>Watch List:</b>\n{wl_str}\n"
            f"\n"
            f"⚠️ <b>Key Risks:</b>\n{risks_str}"
            f"{rl_line}\n"
            f"\n"
            f"📝 <b>Today's Plan:</b>\n  {thesis.summary}\n"
            f"{sep}"
        )
        return msg

    def apply_to_risk_manager(self, thesis: DayThesis, risk_manager) -> None:
        """
        Apply trading mode's size_multiplier and mode flags to risk manager
        for the day. Called once after morning brief is generated.
        """
        try:
            # Apply size multiplier if the risk manager has the attribute
            if hasattr(risk_manager, "size_multiplier"):
                risk_manager.size_multiplier = thesis.size_multiplier
                logger.info(
                    f"[{format_ist_timestamp()}] Risk manager size_multiplier "
                    f"set to {thesis.size_multiplier}x ({thesis.trading_mode})"
                )

            # Apply trading mode
            if hasattr(risk_manager, "trading_mode"):
                risk_manager.trading_mode = thesis.trading_mode

            # DEFENSIVE mode: max 2 trades, 50% size
            if thesis.trading_mode == "DEFENSIVE":
                if hasattr(risk_manager, "max_positions"):
                    risk_manager.max_positions = 2
                logger.warning(
                    f"[{format_ist_timestamp()}] DEFENSIVE mode active — "
                    f"max 2 trades, 0.5x size. VIX={thesis.vix}"
                )
            elif thesis.trading_mode == "CAUTIOUS":
                if hasattr(risk_manager, "max_positions"):
                    risk_manager.max_positions = 4
                logger.info(
                    f"[{format_ist_timestamp()}] CAUTIOUS mode active — "
                    f"reduced size, max 4 trades"
                )

            # Log the full thesis to risk manager for reference
            if hasattr(risk_manager, "day_thesis"):
                risk_manager.day_thesis = thesis

        except Exception as e:
            logger.error(
                f"[{format_ist_timestamp()}] apply_to_risk_manager failed: {e}",
                exc_info=True,
            )

    # ──────────────────────────────────────────────────────────
    # SCORING METHODS
    # ──────────────────────────────────────────────────────────

    def _score_fii(self) -> Tuple[int, str, float]:
        """
        Returns (score, bias_str, fii_net_cr).
        FII net > 1000 Cr: +20 | net < -1000 Cr: -20
        Bias: "BUYING" | "SELLING" | "NEUTRAL"
        """
        if self._fii is None:
            return 0, "NEUTRAL", 0.0
        try:
            flow_bias = self._fii.get_flow_bias()
            today     = flow_bias.today_flow
            fii_net   = today.fii_net if today else 0.0
            score     = 0

            if fii_net > 1000:
                score = SCORE_FII_STRONG_BUY
                bias  = "BUYING"
            elif fii_net > 500:
                score = 10
                bias  = "BUYING"
            elif fii_net > -500:
                score = 0
                bias  = "NEUTRAL"
            elif fii_net > -1000:
                score = -10
                bias  = "SELLING"
            else:
                score = SCORE_FII_STRONG_SELL
                bias  = "SELLING"

            return score, bias, fii_net

        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] FII scoring failed: {e}")
            return 0, "NEUTRAL", 0.0

    def _score_oc(self) -> Tuple[int, str, float]:
        """
        Returns (score, bias_str, pcr).
        OC BULLISH: +15 | BEARISH: -15
        Bias: "BULLISH" | "BEARISH" | "NEUTRAL"
        """
        if self._oc is None:
            return 0, "NEUTRAL", 1.0
        try:
            result = self._oc.analyze("NIFTY")
            if result is None:
                return 0, "NEUTRAL", 1.0

            bias  = result.direction_bias  # "BULLISH", "BEARISH", "NEUTRAL"
            score = 0
            if bias == "BULLISH":
                score = SCORE_OC_BULLISH
            elif bias == "BEARISH":
                score = SCORE_OC_BEARISH

            return score, bias, result.pcr

        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] OC scoring failed: {e}")
            return 0, "NEUTRAL", 1.0

    def _score_overnight(self) -> Tuple[int, float, float, float]:
        """
        Returns (score, vix, spy_gap_pct, spy_prev_close).
        SPY gap > +0.5%: +15 | < -0.5%: -15
        VIX < 14: +10 | VIX > 22: -10
        """
        default_vix  = 15.0
        default_gap  = 0.0
        # Live SPY prev_close fallback — only used if overnight_analyzer fails to run
        try:
            from data_fetch_alpaca import get_data_fetcher
            _q = get_data_fetcher().get_quote("SPY")
            default_spy = float(_q.get("prev_close") or _q.get("ltp") or 530.0)
        except Exception:
            default_spy = 530.0   # rough 2026 SPY level — only used if all APIs fail

        if self._overnight is None:
            return 0, default_vix, default_gap, default_spy

        try:
            if self._overnight._today_analysis is None:
                self._overnight.run_analysis()   # was .run() — method is run_analysis()

            analysis  = self._overnight._today_analysis or {}
            vix_data  = analysis.get("vix", {})
            spy_data  = analysis.get("spy_gap", {})

            vix       = float(vix_data.get("vix",       default_vix))
            gap_pct   = float(spy_data.get("gap_pct",   default_gap))
            spy_prev  = float(spy_data.get("prev_close", default_spy))

            score = 0

            # SPY gap direction
            if gap_pct > 0.5:
                score += SCORE_SPY_GAP_UP
            elif gap_pct > 0.2:
                score += 8
            elif gap_pct < -0.5:
                score += SCORE_SPY_GAP_DOWN  # negative
            elif gap_pct < -0.2:
                score -= 8

            # VIX
            if vix < 14:
                score += SCORE_VIX_LOW
            elif vix < 18:
                score += 5
            elif vix > 22:
                score += SCORE_VIX_HIGH  # negative
            elif vix > 18:
                score -= 5

            return score, vix, gap_pct, spy_prev

        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Overnight scoring failed: {e}")
            return 0, default_vix, default_gap, default_spy

    def _get_events_risk(self) -> Tuple[List[str], int]:
        """
        Returns (risks: List[str], severity_score: int).
        severity_score: 0=none, 1=MEDIUM events, 2=HIGH events, 3=EXTREME events
        Applies SCORE_EVENT_PENALTY (-10) if any major event today.
        """
        risks    = []
        severity = 0

        # From economic calendar
        if self._calendar is not None:
            try:
                events = self._calendar.get_today_events()
                for ev in events:
                    # ev is EconomicEvent object; support both object and dict
                    if hasattr(ev, "impact"):
                        impact = ev.impact
                        name   = ev.name
                        time_s = getattr(ev, "release_time_et", "")
                    else:
                        impact = ev.get("impact", "MEDIUM")
                        name   = ev.get("event", "")
                        time_s = ev.get("time_ist", "")
                    time_suffix = f" at {time_s} IST" if time_s and time_s != "10:00" else ""

                    if impact == "HOLIDAY":
                        risks.append(f"NSE Holiday: {name}")
                        severity = max(severity, 3)
                    elif impact == "EXTREME":
                        risks.append(f"EXTREME EVENT: {name}{time_suffix}")
                        severity = max(severity, 3)
                    elif impact == "HIGH":
                        risks.append(f"{name}{time_suffix}")
                        severity = max(severity, 2)
                    elif impact == "MEDIUM":
                        risks.append(f"{name}{time_suffix}")
                        severity = max(severity, 1)
            except Exception as e:
                logger.warning(f"[{format_ist_timestamp()}] Calendar risk fetch failed: {e}")

        # From overnight analyzer's key_risks
        if self._overnight is not None:
            try:
                overnight_risks = self._overnight.get_key_risks()
                for r in overnight_risks:
                    if r not in risks:
                        risks.append(r)
            except Exception as e:
                logger.warning(f"[{format_ist_timestamp()}] Overnight risks fetch failed: {e}")

        # From news filter
        if self._news is not None:
            try:
                # Check if there's a blackout right now (event window)
                blocked, reason = False, ""
                if hasattr(self._news, "is_safe_to_trade"):
                    is_safe = self._news.is_safe_to_trade()
                    if not is_safe:
                        risks.append("News blackout active — high-impact event window")
                        severity = max(severity, 2)
            except Exception as e:
                logger.debug(f"[{format_ist_timestamp()}] News filter check failed: {e}")

        # Deduplicate while preserving order
        seen  = set()
        dedup = []
        for r in risks:
            key = r[:60].lower()
            if key not in seen:
                seen.add(key)
                dedup.append(r)

        return dedup[:8], severity

    def _get_nifty_range(
        self,
        spy_prev: float,
        vix: float,
        spy_gap_change: float = 0.0,
    ) -> Tuple[float, float]:
        """
        VIX-based daily range estimate for SPY (industry standard).
        1-sigma daily move = VIX / (sqrt(252) * 100)
        Adjusted for SPY pre-market gap direction.
        """
        if spy_prev <= 0:
            spy_prev = 500.0

        daily_move_pct = vix / (16 * math.sqrt(252)) * 100

        range_low  = spy_prev * (1 - daily_move_pct / 100)
        range_high = spy_prev * (1 + daily_move_pct / 100)

        if spy_gap_change > 0:
            range_high *= (1 + spy_gap_change / 200)
        elif spy_gap_change < 0:
            range_low  *= (1 + spy_gap_change / 200)

        return range_low, range_high

    def _determine_mode(
        self,
        bias_score: int,
        vix: float,
        event_severity: int,
    ) -> Tuple[str, float]:
        """
        Determines trading mode and size multiplier.

        DEFENSIVE  → VIX > 25, OR event_severity >= 3 (EXTREME/HOLIDAY/Budget/RBI),
                     OR |bias_score| gap > 2%
        CAUTIOUS   → Any: VIX > 20, bias_score < 0 strongly, event_severity == 2 (HIGH)
        AGGRESSIVE → FII bullish + OC bullish + gift up + VIX < 15 + no events
                     (approximated as bias_score > 50, VIX < 15, no events)
        NORMAL     → Everything else
        """
        # DEFENSIVE first (override everything)
        if vix > 25 or event_severity >= 3:
            return "DEFENSIVE", TRADING_MODES["DEFENSIVE"]

        # CAUTIOUS
        if (vix > 20
                or event_severity >= 2
                or bias_score < -20):
            return "CAUTIOUS", TRADING_MODES["CAUTIOUS"]

        # AGGRESSIVE — all factors positive, low risk
        if (bias_score > 50
                and vix < 15
                and event_severity == 0):
            return "AGGRESSIVE", TRADING_MODES["AGGRESSIVE"]

        # AGGRESSIVE even if bias not super high but conditions clean
        if (bias_score > 30
                and vix < 15
                and event_severity == 0):
            return "AGGRESSIVE", TRADING_MODES["AGGRESSIVE"]

        # Default: NORMAL
        return "NORMAL", TRADING_MODES["NORMAL"]

    def _get_sectors(self) -> Tuple[List[str], List[str], int]:
        """
        Returns (hot_sectors, avoid_sectors, sector_score).
        sector_score: +10 if at least one HOT sector, 0 otherwise.
        """
        hot    = []
        avoid  = []
        s_score = 0

        if self._sectors is None:
            return hot, avoid, s_score

        try:
            scores = self._sectors.score_all_sectors()
            if scores:
                # Top 3 sectors
                hot = [s.sector for s in scores[:3]]
                # Bottom 2 sectors
                avoid = [s.sector for s in scores[-2:]]
                # Score boost if top sector is HOT
                if scores[0].trend == "HOT":
                    s_score = SCORE_SECTOR_HOT
                elif scores[0].trend == "WARM":
                    s_score = 5
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Sector scoring failed: {e}")

        return hot, avoid, s_score

    def _get_rl_status(self) -> str:
        """Get RL brain status message. Gracefully returns empty string if unavailable."""
        try:
            from rl_agent import LakshKingRL
            return LakshKingRL.status_message()
        except Exception as e:
            logger.debug(f"[{format_ist_timestamp()}] RL status failed: {e}")
            return ""

    def _build_top_watchlist(
        self,
        base_watchlist: List[str],
        hot_sectors: List[str],
    ) -> List[str]:
        """
        Build top 5 watchlist combining:
        1. Stocks from hot sectors (sector leaders)
        2. Gapped stocks (potential ORB candidates)
        3. Original watchlist filtered to hot sectors
        """
        from sector_rotation import STOCK_TO_SECTOR

        # Build reverse map: ETF ticker → list of stocks
        _sector_stocks: Dict[str, List[str]] = {}
        for sym, etf in STOCK_TO_SECTOR.items():
            _sector_stocks.setdefault(etf, []).append(sym)

        candidates: List[str] = []
        seen: set = set()

        # Add top 2 stocks from each hot sector
        for sector in hot_sectors[:3]:
            stocks = _sector_stocks.get(sector, [])
            for sym in stocks[:2]:
                if sym not in seen:
                    candidates.append(sym)
                    seen.add(sym)

        # Add gap stocks (if gap analyzer available)
        if self._gap is not None:
            try:
                gaps = self._gap._gaps
                # Large or extreme gapped stocks — wait-and-watch, not immediate
                # Only add MEDIUM/LARGE gap stocks as candidates (not EXTREME — those are avoided)
                from gap_analyzer import GapCategory
                for sym, gap_info in gaps.items():
                    if (gap_info.category in (GapCategory.MEDIUM, GapCategory.LARGE)
                            and sym not in seen):
                        candidates.append(sym)
                        seen.add(sym)
            except Exception as e:
                logger.debug(f"[{format_ist_timestamp()}] Gap watchlist build failed: {e}")

        # Add from base watchlist that belong to hot sectors
        if base_watchlist and hot_sectors:
            hot_set = set(hot_sectors)
            for sym in base_watchlist:
                if sym not in seen:
                    try:
                        sector = self._sectors.get_sector_for_symbol(sym) if self._sectors else "OTHER"
                        if sector in hot_set:
                            candidates.append(sym)
                            seen.add(sym)
                    except Exception as _e:
                        logger.debug(f"[suppressed] {_e}")

        # Fill remaining from base watchlist
        for sym in base_watchlist:
            if sym not in seen and len(candidates) < 10:
                candidates.append(sym)
                seen.add(sym)

        return candidates[:5]

    def _calc_bias_score(
        self,
        fii_score: int,
        oc_score: int,
        overnight_score: int,
        sector_score: int,
        event_severity: int,
    ) -> int:
        """
        Combine component scores into composite bias_score (-100 to +100).
        Event severity applies the uncertainty penalty.
        """
        score = fii_score + oc_score + overnight_score + sector_score

        # Apply event penalty for each high-severity event
        if event_severity >= 3:
            score += SCORE_EVENT_PENALTY * 2  # -20 for extreme events
        elif event_severity >= 2:
            score += SCORE_EVENT_PENALTY      # -10 for high events
        elif event_severity >= 1:
            score += SCORE_EVENT_PENALTY // 2  # -5 for medium events

        return score

    def _build_summary(
        self,
        thesis: DayThesis,
        fii_net: float,
        pcr: float,
    ) -> str:
        """
        Build 2-sentence human-readable summary for the day.
        Describes the key driver and the recommended action.
        """
        # Sentence 1: Key drivers
        drivers = []

        if thesis.fii_bias == "BUYING" and fii_net > 500:
            drivers.append(f"FII buying (₹{fii_net:,.0f}Cr)")
        elif thesis.fii_bias == "SELLING" and fii_net < -500:
            drivers.append(f"FII selling (₹{fii_net:,.0f}Cr)")

        if thesis.oc_bias == "BULLISH":
            drivers.append(f"bullish OC (PCR {pcr:.2f})")
        elif thesis.oc_bias == "BEARISH":
            drivers.append(f"bearish OC (PCR {pcr:.2f})")

        if thesis.spy_gap_change > 0.5:
            drivers.append(f"SPY gap up {thesis.spy_gap_change:+.2f}%")
        elif thesis.spy_gap_change < -0.5:
            drivers.append(f"SPY gap down {thesis.spy_gap_change:+.2f}%")

        if thesis.hot_sectors:
            drivers.append(f"{thesis.hot_sectors[0]} leading")

        if drivers:
            driver_str = " + ".join(drivers[:3])
        else:
            driver_str = "mixed signals"

        # Sentence 2: Action recommendation
        if thesis.trading_mode == "AGGRESSIVE":
            action = (
                f"Aggressive mode — full position sizes. "
                f"Focus on {', '.join(thesis.hot_sectors[:2])} breakouts."
            )
        elif thesis.trading_mode == "DEFENSIVE":
            action = (
                f"Defensive mode — only A+ signals, 50% position size, max 2 trades. "
                f"VIX={thesis.vix} or major event active."
            )
        elif thesis.trading_mode == "CAUTIOUS":
            action = (
                f"Cautious mode — reduced size (0.7x), wait for high-confidence setups. "
                f"{'Avoid ' + ', '.join(thesis.avoid_sectors[:2]) + '.' if thesis.avoid_sectors else ''}"
            )
        else:
            if thesis.market_bias == "BULLISH":
                action = (
                    f"Normal mode — prefer LONG setups, buy dips toward VWAP. "
                    f"{'Focus on ' + ', '.join(thesis.hot_sectors[:2]) + '.' if thesis.hot_sectors else ''}"
                )
            elif thesis.market_bias == "BEARISH":
                action = (
                    f"Normal mode — prefer SHORT setups, sell rallies. "
                    f"Avoid longs in {', '.join(thesis.avoid_sectors[:2]) + '.' if thesis.avoid_sectors else 'weak sectors.'}"
                )
            else:
                action = (
                    f"Normal mode — wait for ORB (9:30-9:45) to confirm direction. "
                    f"Trade only high-confidence setups (score > 75)."
                )

        return f"{driver_str.capitalize()}. {action}"


# ============================================================
# SINGLETON
# ============================================================

_intelligence: Optional[MorningIntelligence] = None


def get_morning_intelligence(**kwargs) -> MorningIntelligence:
    """
    Return singleton MorningIntelligence instance.
    Pass component instances as kwargs on first call:
      get_morning_intelligence(
          fii_tracker=..., oc_analyzer=..., sector_rotation=...,
          overnight=..., gap_analyzer=..., calendar=..., news_filter=...
      )
    Subsequent calls return the same instance.
    """
    global _intelligence
    if _intelligence is None:
        _intelligence = MorningIntelligence(**kwargs)
    return _intelligence


# ============================================================
# STANDALONE RUNNER
# ============================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
    )

    print("=== Morning Intelligence — Standalone Test ===\n")

    # Instantiate all available data sources
    fii_tracker     = None
    oc_analyzer     = None
    sector_rotation = None
    overnight       = None
    gap_analyzer    = None
    calendar        = None
    news_filter_obj = None

    try:
        from fii_dii_tracker import FIIDIITracker
        fii_tracker = FIIDIITracker()
        print("✓ FII tracker loaded")
    except Exception as e:
        print(f"✗ FII tracker: {e}")

    try:
        from option_chain import OptionChainAnalyzer
        oc_analyzer = OptionChainAnalyzer()
        print("✓ Option chain analyzer loaded")
    except Exception as e:
        print(f"✗ Option chain: {e}")

    try:
        from sector_rotation import SectorRotationEngine
        sector_rotation = SectorRotationEngine()
        print("✓ Sector rotation engine loaded")
    except Exception as e:
        print(f"✗ Sector rotation: {e}")

    try:
        from overnight_analyzer import OvernightAnalyzer
        overnight = OvernightAnalyzer()
        print("✓ Overnight analyzer loaded")
    except Exception as e:
        print(f"✗ Overnight analyzer: {e}")

    try:
        from gap_analyzer import GapAnalyzer
        gap_analyzer = GapAnalyzer()
        print("✓ Gap analyzer loaded")
    except Exception as e:
        print(f"✗ Gap analyzer: {e}")

    try:
        from economic_calendar import EconomicCalendar
        calendar = EconomicCalendar()
        print("✓ Economic calendar loaded")
    except Exception as e:
        print(f"✗ Economic calendar: {e}")

    try:
        import os
        from news_filter import NewsFilter
        news_filter_obj = NewsFilter(news_api_key=os.getenv("NEWS_API_KEY", ""))
        print("✓ News filter loaded")
    except Exception as e:
        print(f"✗ News filter: {e}")

    print()

    intel = MorningIntelligence(
        fii_tracker     = fii_tracker,
        oc_analyzer     = oc_analyzer,
        sector_rotation = sector_rotation,
        overnight       = overnight,
        gap_analyzer    = gap_analyzer,
        calendar        = calendar,
        news_filter     = news_filter_obj,
    )

    thesis = intel.generate(watchlist=["HDFCBANK", "RELIANCE", "ICICIBANK", "TCS", "INFY"])

    print("\n=== DAY THESIS ===")
    print(f"Date:          {thesis.date}")
    print(f"Market Bias:   {thesis.market_bias} (score: {thesis.bias_score:+d})")
    print(f"Trading Mode:  {thesis.trading_mode} ({thesis.size_multiplier}x)")
    print(f"SPY Range:     ${thesis.spy_range_low:,.2f} – ${thesis.spy_range_high:,.2f}")
    print(f"VIX:           {thesis.vix}")
    print(f"SPY Gap:       {thesis.spy_gap_change:+.2f}%")
    print(f"FII Bias:      {thesis.fii_bias}")
    print(f"OC Bias:       {thesis.oc_bias}")
    print(f"Hot Sectors:   {thesis.hot_sectors}")
    print(f"Avoid:         {thesis.avoid_sectors}")
    print(f"Watchlist:     {thesis.top_watchlist}")
    print(f"Key Risks:     {thesis.key_risks}")
    print(f"\nSummary: {thesis.summary}")

    print("\n=== TELEGRAM MESSAGE ===")
    print(intel.format_telegram(thesis))
