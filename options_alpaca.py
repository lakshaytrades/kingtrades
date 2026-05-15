"""
options_alpaca.py — US Options Chain Data via Alpaca

Provides:
  - Option chain fetching (calls + puts) with full Greeks
  - IV Rank / IV Percentile calculation (30-day lookback)
  - Best-strike selector (ATM/slight-OTM for scalping)
  - Unusual options activity scanner (OI + volume spikes)
  - Bid-ask spread quality filter

Option symbol format (OCC standard):
  {underlying}{YY}{MM}{DD}{C/P}{strike * 1000 zero-padded to 8 digits}
  e.g. NVDA250519C00900000  = NVDA call, May 19 2025, $900 strike

Requires: Alpaca Options subscription (free tier gives indicative data)
"""

import logging
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from auth_alpaca import get_auth_manager
from utils import get_current_et_time, format_et_timestamp

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except ImportError:
    import pytz
    ET = pytz.timezone("America/New_York")


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OptionContract:
    symbol:        str          # OCC option symbol  e.g. NVDA250519C00900000
    underlying:    str          # e.g. NVDA
    contract_type: str          # "call" or "put"
    strike:        float
    expiration:    date
    dte:           int          # days to expiration (0 = 0DTE)

    # Quote
    bid:           float = 0.0
    ask:           float = 0.0
    mid:           float = 0.0
    last:          float = 0.0
    volume:        int   = 0
    open_interest: int   = 0

    # Greeks
    delta:  float = 0.0
    gamma:  float = 0.0
    theta:  float = 0.0
    vega:   float = 0.0

    # Vol
    iv:     float = 0.0         # implied volatility (decimal, e.g. 0.45 = 45%)
    iv_rank: float = 0.0        # 0-100 percentile vs last 30 days

    @property
    def spread_pct(self) -> float:
        """Bid-ask spread as % of mid — proxy for liquidity."""
        if self.mid <= 0:
            return 999.0
        return (self.ask - self.bid) / self.mid * 100

    @property
    def moneyness(self) -> str:
        return "ATM" if abs(self.delta) >= 0.4 else "OTM"

    @property
    def is_tradeable(self) -> bool:
        """Basic liquidity/quality gate before entering."""
        return (
            self.bid > 0.05         # min $0.05 premium
            and self.ask > 0
            and self.spread_pct < 20.0   # spread < 20% of mid
            and abs(self.delta) >= 0.20  # not too far OTM
            and self.volume > 5          # some activity today
        )


@dataclass
class OptionsSignal:
    """Options trade recommendation derived from underlying stock signal."""
    underlying:    str
    contract:      OptionContract
    direction:     str          # "LONG_CALL" or "LONG_PUT"
    contracts_qty: int = 1
    max_premium:   float = 0.0  # total premium to pay (contracts * ask * 100)
    stop_pct:      float = 45.0 # exit if premium falls by this %
    target1_pct:   float = 80.0 # take 60% off at this gain %
    target2_pct:   float = 150.0 # exit rest at this gain %
    confidence:    float = 0.0
    rationale:     str   = ""
    signal_score:  float = 0.0  # underlying stock signal score
    entry_price:   float = 0.0  # option mid at entry
    patterns:      List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# OPTIONS DATA FETCHER
# ─────────────────────────────────────────────────────────────────────────────

class AlpacaOptionsData:
    """
    Fetches live option chains and computes Greeks + IV analysis.
    Uses Alpaca's OptionHistoricalDataClient (indicative feed on free tier).
    """

    CHAIN_CACHE_TTL = 60     # seconds between chain refreshes
    IV_HISTORY_DAYS = 20     # lookback for IV rank

    def __init__(self):
        self._auth = get_auth_manager()
        self._chain_cache: Dict[str, Tuple[float, List[OptionContract]]] = {}
        self._iv_history:  Dict[str, List[float]] = {}

    def _get_option_client(self):
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient
            return OptionHistoricalDataClient(
                api_key    = self._auth._api_key,
                secret_key = self._auth._secret_key,
            )
        except Exception as e:
            logger.debug(f"OptionHistoricalDataClient init failed: {e}")
            return None

    # ──────────────────────────────────────────────────────────────
    # CHAIN FETCH
    # ──────────────────────────────────────────────────────────────

    def get_option_chain(
        self,
        symbol:       str,
        contract_type: str = "both",  # "call", "put", "both"
        dte_min:      int  = 0,
        dte_max:      int  = 5,
        strike_range_pct: float = 10.0,  # only fetch strikes ±10% of stock price
        underlying_price: float = 0.0,
    ) -> List[OptionContract]:
        """
        Return list of OptionContract objects for the given underlying.
        Filters by DTE and strike proximity to current price.
        """
        now_mono = _time.monotonic()
        cache_key = f"{symbol}_{contract_type}_{dte_min}_{dte_max}"
        if cache_key in self._chain_cache:
            ts, cached = self._chain_cache[cache_key]
            if now_mono - ts < self.CHAIN_CACHE_TTL:
                return cached

        client = self._get_option_client()
        if client is None:
            return []

        try:
            from alpaca.data.requests import OptionChainRequest
            from alpaca.trading.enums import ContractType

            today    = get_current_et_time().date()
            exp_gte  = today + timedelta(days=dte_min)
            exp_lte  = today + timedelta(days=dte_max + 1)

            # Strike filter (if we have underlying price)
            strike_gte = None
            strike_lte = None
            if underlying_price > 0:
                r = underlying_price * strike_range_pct / 100
                strike_gte = underlying_price - r
                strike_lte = underlying_price + r

            ct_enum = None
            if contract_type == "call":
                ct_enum = ContractType.CALL
            elif contract_type == "put":
                ct_enum = ContractType.PUT

            req = OptionChainRequest(
                underlying_symbol  = symbol,
                expiration_date_gte= exp_gte,
                expiration_date_lte= exp_lte,
                strike_price_gte   = strike_gte,
                strike_price_lte   = strike_lte,
                type               = ct_enum,
            )

            chain_data = client.get_option_chain(req)
            contracts  = []
            today_et   = today

            for opt_sym, snap in chain_data.items():
                try:
                    contract = self._parse_snapshot(opt_sym, snap, today_et)
                    if contract:
                        contracts.append(contract)
                except Exception as e:
                    logger.debug(f"Snapshot parse error {opt_sym}: {e}")

            # Sort by strike then DTE
            contracts.sort(key=lambda c: (c.expiration, c.strike))

            self._chain_cache[cache_key] = (now_mono, contracts)
            logger.debug(
                f"[{format_et_timestamp()}] Chain {symbol} ({contract_type}): "
                f"{len(contracts)} contracts fetched"
            )
            return contracts

        except Exception as e:
            logger.debug(f"get_option_chain({symbol}) failed: {e}")
            return []

    def _parse_snapshot(self, opt_sym: str, snap, today: date) -> Optional[OptionContract]:
        """Parse an OptionsSnapshot into an OptionContract dataclass."""
        try:
            # Parse OCC symbol: NVDA250519C00900000
            # Format: {sym}{YY}{MM}{DD}{C/P}{8-digit-strike*1000}
            # Find first digit position
            i = 0
            while i < len(opt_sym) and not opt_sym[i].isdigit():
                i += 1
            underlying = opt_sym[:i]
            rest       = opt_sym[i:]
            yy, mm, dd = int(rest[0:2]), int(rest[2:4]), int(rest[4:6])
            cp         = rest[6]
            strike_raw = int(rest[7:])
            strike     = strike_raw / 1000.0
            exp_date   = date(2000 + yy, mm, dd)
            dte        = (exp_date - today).days
            ctype      = "call" if cp == "C" else "put"

            if dte < 0:
                return None

            # Extract quote
            bid = ask = last = 0.0
            volume = oi = 0
            try:
                q = snap.latest_quote
                if q:
                    bid  = float(q.bid_price or 0)
                    ask  = float(q.ask_price or 0)
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")
            try:
                t = snap.latest_trade
                if t:
                    last = float(t.price or 0)
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")
            try:
                volume = int(snap.day.volume or 0) if snap.day else 0
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")
            try:
                oi = int(snap.open_interest or 0)
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")

            mid = (bid + ask) / 2 if (bid + ask) > 0 else last

            # Greeks
            delta = gamma = theta = vega = iv = 0.0
            try:
                g = snap.greeks
                if g:
                    delta = float(g.delta or 0)
                    gamma = float(g.gamma or 0)
                    theta = float(g.theta or 0)
                    vega  = float(g.vega  or 0)
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")
            try:
                iv = float(snap.implied_volatility or 0)
            except Exception as _e:
                logger.debug(f"[suppressed] {_e}")

            return OptionContract(
                symbol        = opt_sym,
                underlying    = underlying,
                contract_type = ctype,
                strike        = strike,
                expiration    = exp_date,
                dte           = dte,
                bid           = bid,
                ask           = ask,
                mid           = mid,
                last          = last,
                volume        = volume,
                open_interest = oi,
                delta         = delta,
                gamma         = gamma,
                theta         = theta,
                vega          = vega,
                iv            = iv,
            )
        except Exception as e:
            logger.debug(f"_parse_snapshot {opt_sym}: {e}")
            return None

    # ──────────────────────────────────────────────────────────────
    # BEST STRIKE SELECTOR
    # ──────────────────────────────────────────────────────────────

    def find_best_contract(
        self,
        symbol:           str,
        underlying_price: float,
        direction:        str,    # "LONG" or "SHORT" (from stock signal)
        max_premium:      float = 500.0,   # max total premium per trade
        preferred_dte:    int   = 1,       # prefer 0DTE (0) or weekly (1-5)
    ) -> Optional[OptionContract]:
        """
        Given a stock momentum signal, find the best option contract to buy.

        Strategy:
          - LONG  signal → buy CALL
          - SHORT signal → buy PUT
          - Prefer delta 0.35-0.60 (near-ATM, good leverage + reasonable premium)
          - Prefer DTE = today (0DTE) for scalps, or 1-3 days for swing
          - Filter: spread < 15%, tradeable, affordable
        """
        ctype = "call" if direction == "LONG" else "put"

        contracts = self.get_option_chain(
            symbol            = symbol,
            contract_type     = ctype,
            dte_min           = 0,
            dte_max           = max(preferred_dte + 2, 5),
            strike_range_pct  = 8.0,
            underlying_price  = underlying_price,
        )

        if not contracts:
            logger.debug(f"No {ctype} contracts found for {symbol}")
            return None

        # Score each contract
        best: Optional[OptionContract] = None
        best_score = -999.0

        for c in contracts:
            if not c.is_tradeable:
                continue

            # Cost filter
            total_premium = c.ask * 100  # 1 contract = 100 shares
            if total_premium > max_premium:
                continue
            if total_premium < 5:  # too cheap = garbage
                continue

            score = 0.0

            # Delta score: prefer 0.35-0.60
            abs_delta = abs(c.delta)
            if 0.35 <= abs_delta <= 0.60:
                score += 40
            elif 0.25 <= abs_delta < 0.35:
                score += 20
            elif 0.60 < abs_delta <= 0.75:
                score += 25
            else:
                score -= 20  # too far ITM or OTM

            # DTE score: prefer preferred_dte
            dte_diff = abs(c.dte - preferred_dte)
            score += max(0, 30 - dte_diff * 8)

            # Spread quality: tighter is better
            score += max(0, 20 - c.spread_pct)

            # Volume/OI: prefer liquid contracts
            if c.volume > 100:
                score += 10
            elif c.volume > 20:
                score += 5

            # IV not too high (we're buying, so cheap IV is better)
            if c.iv < 0.50:
                score += 10
            elif c.iv > 1.0:
                score -= 15  # very expensive premium

            if score > best_score:
                best_score = score
                best = c

        if best:
            logger.info(
                f"[{format_et_timestamp()}] Best {ctype} for {symbol}: "
                f"{best.symbol} strike={best.strike} dte={best.dte} "
                f"delta={best.delta:.2f} mid=${best.mid:.2f} spread={best.spread_pct:.1f}%"
            )
        return best

    # ──────────────────────────────────────────────────────────────
    # IV RANK
    # ──────────────────────────────────────────────────────────────

    def get_iv_rank(self, symbol: str, current_iv: float) -> float:
        """
        IV Rank = (current IV - 20d low IV) / (20d high IV - 20d low IV) × 100
        Returns 0-100. Low rank (<30) = cheap options (good to buy).
        """
        history = self._iv_history.get(symbol, [])
        if len(history) < 5:
            return 50.0   # neutral if insufficient history

        lo = min(history)
        hi = max(history)
        if hi - lo < 0.01:
            return 50.0

        rank = (current_iv - lo) / (hi - lo) * 100
        return round(max(0.0, min(100.0, rank)), 1)

    def record_iv(self, symbol: str, iv: float) -> None:
        """Update IV history for rank calculation."""
        if symbol not in self._iv_history:
            self._iv_history[symbol] = []
        self._iv_history[symbol].append(iv)
        # Keep last 20 data points
        if len(self._iv_history[symbol]) > 20:
            self._iv_history[symbol].pop(0)

    # ──────────────────────────────────────────────────────────────
    # UNUSUAL OPTIONS ACTIVITY
    # ──────────────────────────────────────────────────────────────

    def scan_unusual_activity(
        self,
        symbols: List[str],
        vol_oi_threshold: float = 2.0,  # volume / OI ratio
    ) -> List[Dict]:
        """
        Scan for unusual options activity (volume >> open interest).
        Returns list of {symbol, type, strike, volume, oi, ratio, direction_bias}.
        """
        results = []
        for sym in symbols:
            try:
                chain = self.get_option_chain(
                    sym, contract_type="both", dte_min=0, dte_max=7,
                    strike_range_pct=15
                )
                for c in chain:
                    if c.open_interest <= 0 or c.volume <= 10:
                        continue
                    ratio = c.volume / c.open_interest
                    if ratio >= vol_oi_threshold:
                        results.append({
                            "symbol":    sym,
                            "opt_sym":   c.symbol,
                            "type":      c.contract_type,
                            "strike":    c.strike,
                            "dte":       c.dte,
                            "volume":    c.volume,
                            "oi":        c.open_interest,
                            "vol_oi":    round(ratio, 2),
                            "delta":     c.delta,
                            "direction_bias": "LONG" if c.contract_type == "call" else "SHORT",
                        })
            except Exception as e:
                logger.debug(f"UOA scan {sym}: {e}")

        # Sort by volume/OI ratio descending
        results.sort(key=lambda x: x["vol_oi"], reverse=True)
        return results[:20]  # top 20


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_options_data: Optional[AlpacaOptionsData] = None


def get_options_data() -> AlpacaOptionsData:
    global _options_data
    if _options_data is None:
        _options_data = AlpacaOptionsData()
        logger.info(f"[{format_et_timestamp()}] AlpacaOptionsData initialized")
    return _options_data
