"""
structured_logger.py
Logs all significant trading events as JSON lines to logs/events/YYYY-MM-DD.jsonl
One JSON object per line (standard JSONL format). Machine-readable, never regex-parsed.
"""
import json, time, logging, threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)

class EventType:
    SIGNAL_GENERATED   = "signal_generated"
    SIGNAL_REJECTED    = "signal_rejected"
    ORDER_PLACED       = "order_placed"
    ORDER_FILLED       = "order_filled"
    ORDER_FAILED       = "order_failed"
    POSITION_OPENED    = "position_opened"
    POSITION_CLOSED    = "position_closed"
    TRAILING_STOP_HIT  = "trailing_stop_hit"
    PARTIAL_EXIT       = "partial_exit"
    CIRCUIT_BREAKER    = "circuit_breaker"
    DAILY_LOSS_LIMIT   = "daily_loss_limit"
    RECONCILE_MISMATCH = "reconcile_mismatch"
    BOT_START          = "bot_start"
    BOT_STOP           = "bot_stop"
    MODULE_ERROR       = "module_error"
    BROKER_RETRY       = "broker_retry"
    ADAPTIVE_PARAMS    = "adaptive_params_applied"
    ML_RETRAIN         = "ml_retrain"
    AB_TEST_RESULT     = "ab_test_result"


class StructuredLogger:
    _lock = threading.Lock()

    def __init__(self):
        self._log_dir = Path("logs/events")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._session_id = str(int(time.time()))

    def log(self, event_type: str, data: dict, level: str = "INFO") -> None:
        event = {
            "ts": datetime.now(ET).isoformat(),
            "ts_unix": time.time(),
            "session": self._session_id,
            "type": event_type,
            "level": level,
            **{k: v for k, v in data.items() if v is not None},
        }
        try:
            log_file = self._log_dir / f"{datetime.now(ET).strftime('%Y-%m-%d')}.jsonl"
            with self._lock:
                with open(log_file, "a") as f:
                    f.write(json.dumps(event, default=str) + "\n")
        except Exception as e:
            logger.warning(f"[SLOG] write failed: {e}")

    def log_signal(self, symbol, score, direction, gates_passed, gates_failed, pattern, **kw):
        self.log(EventType.SIGNAL_GENERATED, dict(
            symbol=symbol, score=score, direction=direction,
            gates_passed=gates_passed, gates_failed=gates_failed,
            pattern=pattern, **kw))

    def log_trade(self, event_type, symbol, direction, price, qty,
                  pnl=0.0, reason="", r_multiple=0.0, **kw):
        self.log(event_type, dict(
            symbol=symbol, direction=direction, price=price, qty=qty,
            pnl=pnl, reason=reason, r_multiple=r_multiple, **kw))

    def log_risk(self, event_type, trigger, **details):
        self.log(event_type, {"trigger": trigger, **details}, level="WARNING")

    def query_today(self, event_type: str = None) -> list:
        log_file = self._log_dir / f"{datetime.now(ET).strftime('%Y-%m-%d')}.jsonl"
        events = []
        try:
            if log_file.exists():
                for line in log_file.read_text().splitlines():
                    try:
                        ev = json.loads(line)
                        if event_type is None or ev.get("type") == event_type:
                            events.append(ev)
                    except Exception:
                        pass
        except Exception:
            pass
        return events

    def get_today_stats(self) -> dict:
        events  = self.query_today()
        signals = [e for e in events if e["type"] == EventType.SIGNAL_GENERATED]
        closes  = [e for e in events if e["type"] == EventType.POSITION_CLOSED]
        errors  = [e for e in events if e.get("level") in ("ERROR", "CRITICAL")]
        wins    = [e for e in closes if e.get("pnl", 0) > 0]
        return {
            "signals": len(signals),
            "trades":  len(closes),
            "win_rate": round(len(wins) / max(len(closes), 1), 3),
            "errors":  len(errors),
            "avg_score": round(sum(s.get("score", 0) for s in signals) / max(len(signals), 1), 1),
        }


_instance: Optional[StructuredLogger] = None

def get_structured_logger() -> StructuredLogger:
    global _instance
    if _instance is None:
        _instance = StructuredLogger()
    return _instance
