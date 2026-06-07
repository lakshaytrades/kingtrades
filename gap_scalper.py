"""
Gap Scalper — pre-market gap detection and gap-play signal generation.
GAP_GO: gap holds → continue. GAP_FADE: gap fails → fade.
"""
import logging
from dataclasses import dataclass
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)

@dataclass
class GapPlay:
    symbol: str; gap_pct: float; gap_type: str; direction: str
    score: float; prev_close: float; current_price: float
    confidence: float; volume_ratio: float; reason: str

class GapScalper:
    MIN_GAP_PCT=0.015; MIN_VOL_RATIO=2.0; MAX_GAP_PCT=0.15

    def analyze_gap(self, symbol: str, prev_close: float, current_price: float,
                    current_volume: int, avg_volume: float,
                    first_bar_range_pct: float = 0.0) -> Optional[GapPlay]:
        if prev_close==0 or current_price==0: return None
        gap = (current_price-prev_close)/prev_close
        vol_r = current_volume/(avg_volume+1e-10)
        if abs(gap)<self.MIN_GAP_PCT or abs(gap)>self.MAX_GAP_PCT: return None
        if vol_r < self.MIN_VOL_RATIO: return None
        score = 60 + min(20,abs(gap)*200) + min(10,vol_r*2) + min(10,first_bar_range_pct*500)
        if gap > 0:
            if first_bar_range_pct < 0.005:
                gt,dir,reason = "GAP_GO","LONG",f"Gap up {gap*100:.1f}% holding — LONG"; score+=5
            else:
                gt,dir,reason = "GAP_FADE","SHORT",f"Gap up {gap*100:.1f}% failing — SHORT"; score-=5
        else:
            if first_bar_range_pct < 0.005:
                gt,dir,reason = "GAP_DOWN_GO","SHORT",f"Gap down {gap*100:.1f}% holding — SHORT"; score+=5
            else:
                gt,dir,reason = "GAP_DOWN_FADE","LONG",f"Gap down {gap*100:.1f}% reversing — LONG"; score-=5
        score = max(50,min(98,score))
        return GapPlay(symbol=symbol,gap_pct=gap,gap_type=gt,direction=dir,
            score=score,prev_close=prev_close,current_price=current_price,
            confidence=score/100,volume_ratio=vol_r,reason=reason)

    def get_score(self, gap_play: Optional[GapPlay], trade_dir: str) -> float:
        if not gap_play: return 0.0
        return min(10.0,gap_play.score/10.0) if gap_play.direction==trade_dir else -5.0

_gs: Optional[GapScalper] = None
def get_gap_scalper() -> GapScalper:
    global _gs
    if _gs is None: _gs = GapScalper()
    return _gs
