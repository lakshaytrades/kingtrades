"""
market_intelligence_hub.py — PRISM God Mode v32.0: Every Trading Data in the World

Single aggregator for 25 institutional data modules not yet wired in the main pipeline.
All run in parallel via ThreadPoolExecutor. Each module fails open (0.0, 1.0).

Modules aggregated:
  MARKET MICROSTRUCTURE
    1.  dark_pool_proxy        — dark pool accumulation proxy
    2.  synthetic_l2           — synthetic Level 2 depth / round-number walls
    3.  order_flow_analyzer    — OFI (Order Flow Imbalance)
    4.  tape_speed             — bar range velocity + volume urgency
    5.  vsa_engine             — Volume Spread Analysis (Wyckoff)

  SMART MONEY / STRUCTURE
    6.  supply_demand_zones    — Sam Seiden S/D zones (fresh vs used)
    7.  smart_money_advanced   — ICT: liquidity sweeps, breaker blocks, Wyckoff
    8.  vanna_charm_flow       — options dealer vanna/charm hedging pressure
    9.  gex_calculator         — Gamma Exposure pin/repel levels
   10.  short_squeeze_detector — float + SI + volume surge

  QUANTITATIVE / STATISTICAL
   11.  stat_arb_cointegration — Engle-Granger cointegrated pairs z-score
   12.  pca_alpha              — PCA residual alpha (idiosyncratic momentum)
   13.  factor_alpha           — Fama-French / momentum factor screen
   14.  ff_factors             — Fama-French 3-factor real-time signal
   15.  har_rv                 — HAR-RV volatility forecast → size multiplier
   16.  hmm_regime             — Hidden Markov Model market regime
   17.  stl_signals            — STL trend decomposition

  CORPORATE EVENTS / FUNDAMENTALS
   18.  pead_engine            — Post-Earnings Announcement Drift
   19.  event_alpha            — analyst upgrades, dividends, stock splits
   20.  weinstein_stage        — Weinstein Stage Analysis (1-4)

  MACRO / SEASONALITY
   21.  economic_surprise      — FRED macro surprise (CPI, NFP, retail sales)
   22.  seasonal_alpha         — monthly/quarterly seasonal alpha
   23.  seasonality_signals    — OpEx, month-end, quarter-end, Monday fade

  SECTOR / RELATIVE STRENGTH
   24.  sector_rs              — sector-relative momentum score
   25.  microstructure_signals — ORB quality, 52w proximity, float momentum,
                                  tick divergence, z-score, candle streak

Total potential score range: [-80, +130] clipped to ±40 per call.
Size multiplier range: [0.5, 1.5].
"""

import logging
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_HUB_TIMEOUT = 8.0   # seconds — total wall-clock budget for the hub
_HUB_WORKERS = 12    # parallel threads (25 tasks, need more workers)


def _safe(fn, *args, default=(0.0, "n/a"), **kwargs) -> Tuple[float, str]:
    """Run fn(*args) with timeout protection; return default on any failure."""
    try:
        result = fn(*args, **kwargs)
        if isinstance(result, tuple) and len(result) == 2:
            delta, reason = result
            return float(delta), str(reason)
        return default
    except Exception as exc:
        logger.debug(f"[hub suppressed] {fn.__name__}: {exc}")
        return default


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def get_intelligence_hub_boost(
    symbol:    str,
    df_5m,
    df_1h,
    direction: str,
    ltp:       float,
    atr:       float,
    watchlist: Optional[List[str]] = None,
) -> Tuple[float, float, str]:
    """
    Run all 25 unwired market intelligence modules in parallel.

    Returns:
        (score_delta, size_multiplier, reason_summary)

    score_delta:      clipped to [-40, +40]
    size_multiplier:  in [0.5, 1.5]
    reason_summary:   top contributing modules
    """
    if watchlist is None:
        watchlist = [symbol]

    tasks: Dict[str, callable] = {}

    # ── 1. Dark pool proxy ───────────────────────────────────────────────────
    try:
        from dark_pool_proxy import get_dark_pool_score as _dp
        tasks["dark_pool"] = lambda: _safe(_dp, symbol, df_5m, direction)
    except ImportError:
        pass

    # ── 2. Synthetic L2 depth ────────────────────────────────────────────────
    try:
        from synthetic_l2 import get_synthetic_depth_score as _l2
        tasks["l2_depth"] = lambda: _safe(_l2, symbol, ltp, direction, df_5m)
    except ImportError:
        pass

    # ── 3. Order Flow Imbalance ──────────────────────────────────────────────
    try:
        from order_flow_analyzer import get_ofi_score as _ofi
        tasks["ofi"] = lambda: _safe(_ofi, df_5m, direction)
    except ImportError:
        pass

    # ── 4. Tape speed ────────────────────────────────────────────────────────
    try:
        from tape_speed import get_tape_speed_score as _ts
        tasks["tape_speed"] = lambda: _safe(_ts, symbol, df_5m, direction)
    except ImportError:
        pass

    # ── 5. VSA Engine ────────────────────────────────────────────────────────
    try:
        from vsa_engine import get_vsa_score as _vsa
        tasks["vsa"] = lambda: _safe(_vsa, symbol, df_5m, direction)
    except ImportError:
        pass

    # ── 6. Supply / Demand zones ─────────────────────────────────────────────
    try:
        from supply_demand_zones import get_supply_demand_score as _sd
        tasks["supply_demand"] = lambda: _safe(_sd, symbol, df_5m, direction, ltp)
    except ImportError:
        pass

    # ── 7. Smart Money Advanced (ICT) ────────────────────────────────────────
    try:
        from smart_money_advanced import SmartMoneyAdvancedScanner, get_smart_money_scanner
        def _sma_task():
            try:
                scanner = get_smart_money_scanner()
                result  = scanner.scan(symbol, df_5m, direction)
                if result is None:
                    return (0.0, "sma:no-signal")
                return (float(getattr(result, 'score_delta', 0.0)),
                        str(getattr(result, 'reason', 'sma')))
            except Exception as exc:
                return (0.0, f"sma:err({exc})")
        tasks["smart_money_adv"] = _sma_task
    except ImportError:
        pass

    # ── 8. Vanna / Charm flow ────────────────────────────────────────────────
    try:
        from vanna_charm_flow import get_vanna_charm_score as _vc
        tasks["vanna_charm"] = lambda: _safe(_vc, symbol, direction, ltp)
    except ImportError:
        pass

    # ── 9. GEX pin / repel ──────────────────────────────────────────────────
    try:
        from gex_calculator import get_gex_signal as _gex
        tasks["gex"] = lambda: _safe(_gex, symbol, ltp, direction)
    except ImportError:
        pass

    # ── 10. Short squeeze detector ───────────────────────────────────────────
    try:
        from short_squeeze_detector import get_short_squeeze_score as _ss
        def _ss_task():
            vol_ratio = 1.0
            if df_5m is not None and len(df_5m) >= 20:
                try:
                    vc = df_5m["volume"].iloc[-1] if "volume" in df_5m.columns else df_5m["Volume"].iloc[-1]
                    va = df_5m["volume"].iloc[-20:].mean() if "volume" in df_5m.columns else df_5m["Volume"].iloc[-20:].mean()
                    vol_ratio = float(vc) / max(float(va), 1.0)
                except Exception:
                    pass
            return _safe(_ss, symbol, direction, vol_ratio)
        tasks["short_squeeze"] = _ss_task
    except ImportError:
        pass

    # ── 11. Stat arb cointegration ───────────────────────────────────────────
    try:
        from stat_arb_cointegration import get_cointegration_signal as _coint
        tasks["cointegration"] = lambda: _safe(_coint, symbol, direction)
    except ImportError:
        pass

    # ── 12. PCA alpha ────────────────────────────────────────────────────────
    try:
        from pca_alpha import get_pca_alpha_score as _pca
        tasks["pca_alpha"] = lambda: _safe(_pca, symbol, direction, watchlist)
    except ImportError:
        pass

    # ── 13. Factor alpha ─────────────────────────────────────────────────────
    try:
        from factor_alpha import get_factor_score as _fa
        tasks["factor_alpha"] = lambda: _safe(_fa, symbol, direction, watchlist)
    except ImportError:
        pass

    # ── 14. Fama-French factors ──────────────────────────────────────────────
    try:
        from ff_factors import get_factor_signal as _ff
        tasks["ff_factors"] = lambda: _safe(_ff, symbol, direction)
    except ImportError:
        pass

    # ── 15. HAR-RV volatility forecast → size multiplier ────────────────────
    try:
        from har_rv import get_har_rv_forecast as _har, get_har_size_multiplier as _har_sz
        def _har_task() -> Tuple[float, str]:
            sigma_pct, regime = _har(symbol, df_5m)
            # HAR returns a vol forecast — convert to score (lower vol → better entry)
            if regime == "LOW_VOL":
                return (4.0, f"har_rv:LOW_VOL({sigma_pct:.1f}%)")
            elif regime == "HIGH_VOL":
                return (-4.0, f"har_rv:HIGH_VOL({sigma_pct:.1f}%)")
            elif regime == "EXTREME":
                return (-8.0, f"har_rv:EXTREME_VOL({sigma_pct:.1f}%)")
            return (0.0, f"har_rv:NORMAL({sigma_pct:.1f}%)")
        tasks["har_rv"] = _har_task
    except ImportError:
        pass

    # ── 16. HMM regime ───────────────────────────────────────────────────────
    try:
        from hmm_regime import get_hmm_score as _hmm
        tasks["hmm_regime"] = lambda: _safe(_hmm, symbol, df_5m, direction)
    except ImportError:
        pass

    # ── 17. STL trend decomposition ──────────────────────────────────────────
    try:
        from stl_signals import get_stl_trend_score as _stl
        tasks["stl_trend"] = lambda: _safe(_stl, df_5m, direction)
    except ImportError:
        pass

    # ── 18. PEAD (Post-Earnings Announcement Drift) ──────────────────────────
    try:
        from pead_engine import get_pead_score as _pead
        tasks["pead"] = lambda: _safe(_pead, symbol, direction)
    except ImportError:
        pass

    # ── 19. Event alpha (analyst upgrades, dividends, splits) ────────────────
    try:
        from event_alpha import (
            get_analyst_signal as _analyst,
            get_dividend_capture_score as _div,
            get_split_signal as _split,
        )
        def _event_task() -> Tuple[float, str]:
            a_d, a_r = _safe(_analyst, symbol, direction)
            d_d, d_r = _safe(_div, symbol, direction)
            s_d, s_r = _safe(_split, symbol, direction)
            total = a_d + d_d + s_d
            reasons = [r for r, d in [(a_r, a_d), (d_r, d_d), (s_r, s_d)] if d != 0.0]
            return (total, "|".join(reasons) if reasons else "event:neutral")
        tasks["event_alpha"] = _event_task
    except ImportError:
        pass

    # ── 20. Weinstein Stage Analysis ─────────────────────────────────────────
    try:
        from weinstein_stage import get_weinstein_score as _wein
        tasks["weinstein"] = lambda: _safe(_wein, symbol, direction)
    except ImportError:
        pass

    # ── 21. Economic surprise (FRED macro) ───────────────────────────────────
    try:
        from economic_surprise import get_economic_surprise_score as _econ
        tasks["econ_surprise"] = lambda: _safe(_econ, direction)
    except ImportError:
        pass

    # ── 22. Seasonal alpha ───────────────────────────────────────────────────
    try:
        from seasonal_alpha import get_seasonal_score as _seas
        tasks["seasonal"] = lambda: _safe(_seas, symbol, direction)
    except ImportError:
        pass

    # ── 23. Seasonality signals (OpEx, month-end, quarter-end) ───────────────
    try:
        from seasonality_signals import (
            get_opex_score as _opex,
            get_month_end_score as _month_end,
            get_quarter_end_score as _qtr_end,
        )
        def _seas2_task() -> Tuple[float, str]:
            o_d, o_r = _safe(_opex, direction)
            m_d, m_r = _safe(_month_end, direction)
            q_d, q_r = _safe(_qtr_end, direction)
            total = o_d + m_d + q_d
            reasons = [r for r, d in [(o_r, o_d), (m_r, m_d), (q_r, q_d)] if d != 0.0]
            return (total, "|".join(reasons) if reasons else "seas2:neutral")
        tasks["seasonality2"] = _seas2_task
    except ImportError:
        pass

    # ── 24. Sector relative strength ─────────────────────────────────────────
    try:
        from sector_rs import get_sector_rs_score as _srs
        tasks["sector_rs"] = lambda: _safe(_srs, symbol, direction)
    except ImportError:
        pass

    # ── 25. Microstructure signals ───────────────────────────────────────────
    try:
        from microstructure_signals import (
            get_orb_quality_score as _orb_q,
            get_tick_divergence_score as _tick_div,
            get_candle_streak_score as _candle_stk,
        )
        def _micro_task() -> Tuple[float, str]:
            o_d, o_r = _safe(_orb_q, symbol, df_5m, direction, ltp)
            t_d, t_r = _safe(_tick_div, df_5m, direction)
            c_d, c_r = _safe(_candle_stk, df_5m, direction)
            total = o_d + t_d + c_d
            reasons = [r for r, d in [(o_r, o_d), (t_r, t_d), (c_r, c_d)] if d != 0.0]
            return (total, "|".join(reasons) if reasons else "micro:neutral")
        tasks["microstructure"] = _micro_task
    except ImportError:
        pass

    # ── Execute all tasks in parallel ────────────────────────────────────────
    results: Dict[str, Tuple[float, str]] = {}
    if not tasks:
        return 0.0, 1.0, "hub:no-modules"

    with ThreadPoolExecutor(max_workers=_HUB_WORKERS) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        try:
            for future in as_completed(futures, timeout=_HUB_TIMEOUT):
                name = futures[future]
                try:
                    results[name] = future.result(timeout=0.1)
                except Exception as exc:
                    results[name] = (0.0, f"{name}:err")
                    logger.debug(f"[hub module error] {name}: {exc}")
        except TimeoutError:
            # Collect whatever finished; unfinished modules return 0 (fail-open)
            for future, name in futures.items():
                if name not in results:
                    results[name] = (0.0, f"{name}:timeout")
                    logger.debug(f"[hub timeout] {name} did not finish in {_HUB_TIMEOUT}s")

    # ── Aggregate scores ─────────────────────────────────────────────────────
    raw_delta  = sum(v[0] for v in results.values())
    score_delta = float(np.clip(raw_delta, -40.0, 40.0))

    # ── HAR-RV size multiplier (separate sizing channel) ────────────────────
    size_mult = 1.0
    try:
        from har_rv import get_har_rv_forecast as _har_f, get_har_size_multiplier as _har_s
        sigma_pct, _ = _har_f(symbol, df_5m)
        size_mult = float(np.clip(_har_s(sigma_pct), 0.5, 1.5))
    except Exception:
        pass

    # ── Build reason summary (top 5 by absolute magnitude) ──────────────────
    sorted_mods = sorted(results.items(), key=lambda kv: abs(kv[1][0]), reverse=True)
    top_parts = []
    for mod_name, (delta, reason) in sorted_mods[:5]:
        if abs(delta) >= 1.0:
            top_parts.append(f"{mod_name}{delta:+.0f}")
    reason_summary = "hub[" + " ".join(top_parts) + f"] Σ{score_delta:+.0f}" if top_parts else "hub:neutral"

    return score_delta, size_mult, reason_summary
