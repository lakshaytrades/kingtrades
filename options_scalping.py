"""
options_scalping.py — US Options Scalping Strategy Engine

Strategy: Momentum-driven options scalping
  1. Stock signal fires (score ≥ 70, pattern-confirmed)
  2. Find optimal near-ATM contract (0DTE preferred, delta 0.35-0.60)
  3. Check IV rank (prefer IV rank < 60 — buying cheap options)
  4. Check time window (not too close to close for 0DTE theta crush)
  5. Size position: max 1% of capital per trade, max 3 contracts
  6. Place LIMIT order at mid → monitor via background thread

Additional standalone signal source:
  - Unusual Options Activity (UOA) scanner: large volume/OI sweeps
    signal institutional positioning → bot piggybacks the smart money

$200/day target math:
  Capital $5,000 × 1% risk = $50 per options trade max loss
  Average gain target: $100-150 per winning trade (80-150% premium gain)
  Win 2-3 options trades per day = $200-450/day
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

from utils import get_current_et_time, format_et_timestamp

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG CONSTANTS (also in config.py — duplicated here for clarity)
# ─────────────────────────────────────────────────────────────────────────────

OPTIONS_ENABLED             = True
MIN_STOCK_SCORE_FOR_OPTIONS = 70.0   # only buy options when stock signal is strong
MAX_OPTIONS_POSITIONS       = 3      # max simultaneous options positions
MAX_PREMIUM_PCT_OF_CAPITAL  = 0.012  # 1.2% of capital per options trade
MAX_CONTRACTS_PER_TRADE     = 5      # hard cap on contracts
MIN_DELTA                   = 0.25   # not too far OTM
MAX_DELTA                   = 0.75   # not deep ITM (poor leverage)
MAX_IV_RANK                 = 65.0   # avoid buying when options are already expensive
MAX_SPREAD_PCT              = 18.0   # max bid-ask spread quality filter
PREFERRED_DTE               = 1      # prefer 1-day expiry (balance theta vs cost)
UOA_SCAN_INTERVAL_MIN       = 15     # how often to scan for unusual activity (minutes)

# Time windows for options (ET)
OPTIONS_OPEN_ET   = dtime(9, 35)    # start trading options 5 min after open (avoid gap)
OPTIONS_CLOSE_ET  = dtime(15, 45)   # stop new entries 15 min before close
ZERODTE_CUTOFF_ET = dtime(14, 30)   # no NEW 0DTE entries after 2:30 PM ET (theta too high)


class OptionsScalpingEngine:
    """
    Converts stock momentum signals into options trades.
    Also runs its own UOA (unusual options activity) scanner.
    """

    def __init__(self, live_enabled: bool = False):
        self.live_enabled      = live_enabled
        self._last_uoa_scan    = 0.0
        self._uoa_signals: List[Dict] = []
        self._daily_options_pnl  = 0.0
        self._daily_options_count = 0

    # ──────────────────────────────────────────────────────────────
    # MAIN ENTRY: stock signal → options trade
    # ──────────────────────────────────────────────────────────────

    def evaluate_stock_signal(
        self,
        symbol:          str,
        direction:       str,     # "LONG" or "SHORT"
        signal_score:    float,
        entry_price:     float,   # stock price at signal time
        available_capital: float,
        patterns:        List[str] = None,
    ) -> Optional[Dict]:
        """
        Decide whether to trade options on this stock signal.
        Returns action dict or None if no options trade warranted.
        """
        if not OPTIONS_ENABLED:
            return None

        # Gate 1: strong enough signal
        if signal_score < MIN_STOCK_SCORE_FOR_OPTIONS:
            logger.debug(f"Options skip {symbol}: score {signal_score:.0f} < {MIN_STOCK_SCORE_FOR_OPTIONS}")
            return None

        # Gate 2: time window check
        if not self._is_valid_options_time():
            return None

        # Gate 3: not too many open options
        from execution_options_alpaca import get_options_executor
        executor = get_options_executor(self.live_enabled)
        open_opts = executor.get_open_options()
        if len(open_opts) >= MAX_OPTIONS_POSITIONS:
            logger.debug(f"Options skip {symbol}: {len(open_opts)}/{MAX_OPTIONS_POSITIONS} positions open")
            return None

        # Gate 4: find best contract
        max_premium = available_capital * MAX_PREMIUM_PCT_OF_CAPITAL
        max_premium = min(max_premium, 500.0)   # hard cap at $500 total premium

        from options_alpaca import get_options_data
        opt_data = get_options_data()

        now_et = get_current_et_time()
        dte    = PREFERRED_DTE
        # On Mondays/near-expiry, or morning, prefer same-day if available
        if (now_et.hour, now_et.minute) <= (11, 30):
            dte = 0   # morning → 0DTE for maximum leverage

        contract = opt_data.find_best_contract(
            symbol            = symbol,
            underlying_price  = entry_price,
            direction         = direction,
            max_premium       = max_premium / 1,  # per-contract premium
            preferred_dte     = dte,
        )

        if not contract:
            logger.debug(f"Options skip {symbol}: no suitable contract found")
            return None

        # Gate 5: IV rank (don't buy expensive options)
        iv_rank = opt_data.get_iv_rank(symbol, contract.iv)
        opt_data.record_iv(symbol, contract.iv)
        if iv_rank > MAX_IV_RANK:
            logger.info(
                f"Options skip {symbol}: IV rank {iv_rank:.0f} > {MAX_IV_RANK} "
                f"(options too expensive to buy)"
            )
            return None

        # Gate 6: 0DTE time cutoff
        if contract.dte == 0 and now_et.time() >= ZERODTE_CUTOFF_ET:
            logger.debug(f"Options skip {symbol}: 0DTE after {ZERODTE_CUTOFF_ET} cutoff")
            return None

        # Position sizing: how many contracts?
        per_contract_cost = contract.ask * 100   # 1 contract = 100 shares
        if per_contract_cost <= 0:
            return None

        contracts_qty = max(1, int(max_premium / per_contract_cost))
        contracts_qty = min(contracts_qty, MAX_CONTRACTS_PER_TRADE)
        total_cost    = contracts_qty * per_contract_cost

        if total_cost > available_capital * 0.05:  # never more than 5% of capital
            contracts_qty = max(1, int(available_capital * 0.05 / per_contract_cost))

        # Build the options signal
        opt_direction = "LONG_CALL" if direction == "LONG" else "LONG_PUT"

        logger.info(
            f"[{format_et_timestamp()}] OPTIONS SIGNAL: {symbol} {opt_direction} "
            f"| {contract.symbol} | {contracts_qty} contracts @ ${contract.ask:.2f} "
            f"| delta={contract.delta:.2f} iv_rank={iv_rank:.0f} dte={contract.dte} "
            f"| total_cost=${total_cost:.0f} | stock_score={signal_score:.0f}"
        )

        return {
            "opt_symbol":    contract.symbol,
            "underlying":    symbol,
            "direction":     opt_direction,
            "contracts":     contracts_qty,
            "max_premium":   contract.mid,   # limit at mid-price
            "dte":           contract.dte,
            "contract":      contract,
            "iv_rank":       iv_rank,
            "total_cost":    total_cost,
            "signal_score":  signal_score,
            "patterns":      patterns or [],
        }

    def execute_options_signal(self, action: Dict) -> bool:
        """Place the options order from a signal action dict."""
        from execution_options_alpaca import get_options_executor
        executor = get_options_executor(self.live_enabled)

        result = executor.place_entry(
            opt_symbol  = action["opt_symbol"],
            underlying  = action["underlying"],
            direction   = action["direction"],
            contracts   = action["contracts"],
            max_premium = action["max_premium"],
            dte         = action["dte"],
        )

        if result.success:
            self._daily_options_count += 1
            logger.info(
                f"[{format_et_timestamp()}] OPTIONS PLACED: {action['opt_symbol']} "
                f"{action['contracts']}x @ ${result.fill_premium:.2f}"
            )
        return result.success

    # ──────────────────────────────────────────────────────────────
    # UNUSUAL OPTIONS ACTIVITY (UOA) SCANNER
    # ──────────────────────────────────────────────────────────────

    def scan_unusual_activity(
        self,
        symbols:           List[str],
        available_capital: float,
    ) -> List[Dict]:
        """
        Independently scan for unusual options activity.
        High volume/OI ratio = institutional positioning.
        Returns list of executable options signals.
        """
        import time as _t
        now = _t.monotonic()
        if now - self._last_uoa_scan < UOA_SCAN_INTERVAL_MIN * 60:
            return []

        self._last_uoa_scan = now

        if not self._is_valid_options_time():
            return []

        from options_alpaca import get_options_data
        opt_data = get_options_data()

        # Only scan a subset of most liquid symbols for speed
        scan_syms = [s for s in symbols if s in (
            "NVDA", "AAPL", "TSLA", "SPY", "QQQ", "AMD", "META",
            "AMZN", "MSFT", "GOOGL", "NFLX", "COIN"
        )]

        uoa_hits = opt_data.scan_unusual_activity(scan_syms, vol_oi_threshold=2.5)
        signals  = []

        for hit in uoa_hits[:5]:  # top 5 UOA signals
            sym  = hit["symbol"]
            bias = hit["direction_bias"]   # "LONG" or "SHORT"

            # Find a contract to trade on this UOA signal
            from data_fetch_alpaca import get_data_fetcher
            quote = get_data_fetcher().get_quote(sym)
            price = quote.get("ltp", 0)
            if price <= 0:
                continue

            max_p = available_capital * MAX_PREMIUM_PCT_OF_CAPITAL
            contract = opt_data.find_best_contract(
                symbol           = sym,
                underlying_price = price,
                direction        = bias,
                max_premium      = max_p,
                preferred_dte    = 1,
            )
            if not contract or not contract.is_tradeable:
                continue

            iv_rank = opt_data.get_iv_rank(sym, contract.iv)
            if iv_rank > MAX_IV_RANK:
                continue

            per_cost = contract.ask * 100
            qty      = max(1, min(MAX_CONTRACTS_PER_TRADE, int(max_p / per_cost)))

            signals.append({
                "opt_symbol":   contract.symbol,
                "underlying":   sym,
                "direction":    f"LONG_{'CALL' if bias == 'LONG' else 'PUT'}",
                "contracts":    qty,
                "max_premium":  contract.mid,
                "dte":          contract.dte,
                "contract":     contract,
                "iv_rank":      iv_rank,
                "total_cost":   qty * per_cost,
                "signal_score": 65.0 + hit["vol_oi"] * 5,   # UOA score
                "source":       "UOA",
                "uoa_ratio":    hit["vol_oi"],
                "patterns":     [f"UOA_{hit['type'].upper()}"],
            })
            logger.info(
                f"[{format_et_timestamp()}] UOA SIGNAL: {sym} {bias} "
                f"vol/OI={hit['vol_oi']:.1f}x | {contract.symbol} "
                f"delta={contract.delta:.2f} cost=${qty * per_cost:.0f}"
            )

        self._uoa_signals = signals
        return signals

    # ──────────────────────────────────────────────────────────────
    # DAILY P&L TRACKING
    # ──────────────────────────────────────────────────────────────

    def add_options_pnl(self, pnl: float) -> None:
        self._daily_options_pnl += pnl

    def get_daily_summary(self) -> Dict:
        return {
            "options_pnl":   round(self._daily_options_pnl, 2),
            "options_trades": self._daily_options_count,
        }

    def reset_daily(self) -> None:
        self._daily_options_pnl   = 0.0
        self._daily_options_count = 0

    # ──────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────

    def _is_valid_options_time(self) -> bool:
        """True during valid options trading window (ET)."""
        now = get_current_et_time()
        t   = now.time()
        return OPTIONS_OPEN_ET <= t <= OPTIONS_CLOSE_ET

    def close_all(self) -> None:
        """EOD / kill-switch: close all open options positions."""
        from execution_options_alpaca import get_options_executor
        executor = get_options_executor(self.live_enabled)
        executor.close_all_options()


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_engine: Optional[OptionsScalpingEngine] = None


def get_options_scalping_engine(live_enabled: bool = False) -> OptionsScalpingEngine:
    global _engine
    if _engine is None:
        _engine = OptionsScalpingEngine(live_enabled=live_enabled)
        # Start the position monitor
        from execution_options_alpaca import get_options_executor
        get_options_executor(live_enabled).start_monitor()
        logger.info(f"[{format_et_timestamp()}] OptionsScalpingEngine initialized")
    return _engine
