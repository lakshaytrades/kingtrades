"""
OODA Engine — Master observe/orient/decide/act orchestrator.
Coordinates all intelligence layers into a single OODAContext per symbol.
"""

from dataclasses import dataclass, field
from typing import Optional
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class OODAContext:
    symbol: str = ""
    macro_score: float = 0.0          # [-10, +10]  global macro backdrop
    sentiment_score: float = 0.0      # [-10, +10]  market sentiment
    flow_score: float = 0.0           # [-10, +10]  institutional flow
    alt_data_score: float = 0.0       # [-10, +10]  options/RS/microstructure
    regime: str = "UNKNOWN"           # TRENDING_BULL/TRENDING_BEAR/RANGING/VOLATILE/CRISIS
    regime_confidence: float = 0.5
    pattern_confluence: float = 0.0   # [-10, +10]  Wyckoff+Elliott+VSA+MarketProfile
    wyckoff_phase: str = "UNKNOWN"
    elliott_wave: str = "UNKNOWN"
    vsa_signal: str = "NEUTRAL"
    conviction: float = 0.5           # [0, 1] Bayesian posterior
    direction_bias: str = "NEUTRAL"   # BULL/BEAR/NEUTRAL
    confidence_interval: tuple = (0.4, 0.6)
    size_multiplier: float = 1.0      # [0.25, 2.0] position sizing scalar
    urgency: str = "NORMAL"           # URGENT/NORMAL/WAIT
    gamma_score: float = 0.0          # [-10, +10] GEX/gamma walls
    short_squeeze_score: float = 0.0  # [0, 10]
    pead_score: float = 0.0           # [-5, +5]
    seasonal_score: float = 0.0       # [-5, +5]
    smart_money_score: float = 0.0    # [-8, +8]
    hmm_state: str = "UNKNOWN"
    kalman_trend: float = 0.0         # velocity from Kalman filter
    timestamp: float = field(default_factory=time.time)
    reasoning: list = field(default_factory=list)


_CACHE: dict = {}  # symbol -> (OODAContext, expiry_ts)
_CACHE_TTL = 300   # 5 minutes


class OODAEngine:
    def __init__(self):
        self._observe = None
        self._orient = None
        self._secret = None
        self._decide = None
        self._load_layers()

    def _load_layers(self):
        try:
            from observe_layer import ObserveLayer
            self._observe = ObserveLayer()
        except Exception as e:
            logger.warning(f"observe_layer unavailable: {e}")
        try:
            from orient_layer import OrientLayer
            self._orient = OrientLayer()
        except Exception as e:
            logger.warning(f"orient_layer unavailable: {e}")
        try:
            from secret_alpha import SecretAlpha
            self._secret = SecretAlpha()
        except Exception as e:
            logger.warning(f"secret_alpha unavailable: {e}")
        try:
            from decide_layer import DecideLayer
            self._decide = DecideLayer()
        except Exception as e:
            logger.warning(f"decide_layer unavailable: {e}")

    def get_context(self, symbol: str, prices=None, volumes=None) -> OODAContext:
        """Return cached OODAContext or build fresh one."""
        now = time.time()
        if symbol in _CACHE:
            ctx, expiry = _CACHE[symbol]
            if now < expiry:
                return ctx
        ctx = self._build_context(symbol, prices, volumes)
        _CACHE[symbol] = (ctx, now + _CACHE_TTL)
        return ctx

    def _build_context(self, symbol: str, prices=None, volumes=None) -> OODAContext:
        ctx = OODAContext(symbol=symbol)
        reasoning = []

        # === OBSERVE ===
        if self._observe:
            try:
                ctx.macro_score = self._observe.get_macro_score()
                ctx.sentiment_score = self._observe.get_sentiment_score()
                ctx.flow_score = self._observe.get_flow_score()
                ctx.alt_data_score = self._observe.get_alt_data_score(symbol)
                reasoning.append(f"Macro={ctx.macro_score:.1f} Sent={ctx.sentiment_score:.1f} Flow={ctx.flow_score:.1f}")
            except Exception as e:
                logger.warning(f"observe error for {symbol}: {e}")

        # === ORIENT ===
        if self._orient and prices is not None:
            try:
                orient_result = self._orient.analyze(symbol, prices, volumes)
                ctx.regime = orient_result.get("regime", "UNKNOWN")
                ctx.regime_confidence = orient_result.get("regime_confidence", 0.5)
                ctx.wyckoff_phase = orient_result.get("wyckoff_phase", "UNKNOWN")
                ctx.elliott_wave = orient_result.get("elliott_wave", "UNKNOWN")
                ctx.vsa_signal = orient_result.get("vsa_signal", "NEUTRAL")
                ctx.pattern_confluence = orient_result.get("pattern_confluence", 0.0)
                reasoning.append(f"Regime={ctx.regime} Wyckoff={ctx.wyckoff_phase} Elliott={ctx.elliott_wave}")
            except Exception as e:
                logger.warning(f"orient error for {symbol}: {e}")

        # === SECRET ALPHA ===
        if self._secret:
            try:
                alpha = self._secret.get_scores(symbol)
                ctx.gamma_score = alpha.get("gamma_score", 0.0)
                ctx.short_squeeze_score = alpha.get("short_squeeze_score", 0.0)
                ctx.pead_score = alpha.get("pead_score", 0.0)
                ctx.seasonal_score = alpha.get("seasonal_score", 0.0)
                ctx.smart_money_score = alpha.get("smart_money_score", 0.0)
                reasoning.append(f"Gamma={ctx.gamma_score:.1f} ShortSq={ctx.short_squeeze_score:.1f} PEAD={ctx.pead_score:.1f} Season={ctx.seasonal_score:.1f}")
            except Exception as e:
                logger.warning(f"secret_alpha error for {symbol}: {e}")

        # === DECIDE ===
        if self._decide:
            try:
                decision = self._decide.decide(ctx, prices, volumes)
                ctx.conviction = decision.get("conviction", 0.5)
                ctx.direction_bias = decision.get("direction_bias", "NEUTRAL")
                ctx.confidence_interval = decision.get("confidence_interval", (0.4, 0.6))
                ctx.hmm_state = decision.get("hmm_state", "UNKNOWN")
                ctx.kalman_trend = decision.get("kalman_trend", 0.0)
                reasoning.append(f"Conviction={ctx.conviction:.2f} Bias={ctx.direction_bias} HMM={ctx.hmm_state}")
            except Exception as e:
                logger.warning(f"decide error for {symbol}: {e}")

        ctx.size_multiplier = self._compute_size_mult(ctx)
        ctx.urgency = self._compute_urgency(ctx)
        ctx.reasoning = reasoning
        return ctx

    def _compute_size_mult(self, ctx: OODAContext) -> float:
        """Compute position size multiplier from conviction + regime."""
        if ctx.regime == "CRISIS":
            return 0.25
        base = 0.5 + ctx.conviction  # [0.5, 1.5]
        macro_adj = (ctx.macro_score + ctx.sentiment_score + ctx.flow_score) / 30.0
        alpha_adj = (ctx.gamma_score + ctx.short_squeeze_score + ctx.pead_score + ctx.seasonal_score) / 40.0
        mult = base + macro_adj + alpha_adj
        return max(0.25, min(2.0, mult))

    def _compute_urgency(self, ctx: OODAContext) -> str:
        if ctx.conviction > 0.80 and ctx.short_squeeze_score > 7:
            return "URGENT"
        if ctx.conviction < 0.45 or ctx.regime in ("CRISIS", "VOLATILE"):
            return "WAIT"
        return "NORMAL"

    def get_symbol_bias(self, symbol: str) -> str:
        ctx = self.get_context(symbol)
        return ctx.direction_bias

    def get_regime(self) -> str:
        ctx = self.get_context("SPY")
        return ctx.regime

    def invalidate(self, symbol: str):
        _CACHE.pop(symbol, None)


# Module-level singleton
_engine: Optional[OODAEngine] = None

def get_engine() -> OODAEngine:
    global _engine
    if _engine is None:
        _engine = OODAEngine()
    return _engine
