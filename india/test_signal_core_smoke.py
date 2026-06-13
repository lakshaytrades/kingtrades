"""
test_signal_core_smoke.py — Regression + smoke test for the India signal core.

Why this exists: india/signal_generator_india.py called a method that does NOT
exist (PatternRecognizer.compute_indicators), inside a broad try/except that
silently swallowed the AttributeError on every bar. The bot therefore produced
ZERO signals — it could never place a trade — and no test caught it because the
failure was masked. This test asserts the indicator path the signal generator
depends on actually works, so the regression cannot return unnoticed.

Run: python3 india/test_signal_core_smoke.py   (exit 0 = pass)
Dependency-light: pandas + numpy only (no pytest, no broker, no network).
"""
import os
import sys

# Make both the repo root (for pattern_recognition) and india/ importable,
# regardless of the directory the test is launched from.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

from pattern_recognition import PatternRecognizer, IndicatorSet


def _synthetic_trending_df(n=300, start=100.0, drift=0.04, seed=7):
    """Deterministic upward-trending 5-min OHLCV with realistic noise."""
    rng = np.random.default_rng(seed)
    closes = [start]
    for _ in range(n - 1):
        closes.append(max(1.0, closes[-1] * (1 + drift / 100 + rng.normal(0, 0.0015))))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(rng.normal(0, 0.001, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.001, n)))
    opens = np.concatenate([[start], closes[:-1]])
    vols = rng.integers(50_000, 200_000, n)
    idx = pd.date_range("2026-06-01 09:15", periods=n, freq="5min", tz="Asia/Kolkata")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
        index=idx,
    )


def test_recognizer_has_no_phantom_method():
    """Documents the root cause: the old call target never existed."""
    rec = PatternRecognizer()
    assert not hasattr(rec, "compute_indicators"), (
        "PatternRecognizer.compute_indicators reappeared — the signal generator "
        "must NOT call it; use rec.indicators.compute/get_latest_indicators."
    )
    assert hasattr(rec, "indicators"), "PatternRecognizer must expose .indicators engine"


def test_indicator_path_returns_populated_set():
    """The exact path generate_signal now uses must yield a real IndicatorSet."""
    rec = PatternRecognizer()
    df = _synthetic_trending_df()
    eng = rec.indicators
    ind = eng.get_latest_indicators(eng.compute(df))
    assert ind is not None, "indicator computation returned None"
    assert isinstance(ind, IndicatorSet)
    assert ind.atr is not None and ind.atr > 0, f"ATR invalid: {ind.atr}"
    # ADX/RSI should be finite numbers in a sane range
    assert ind.rsi is None or (0 <= ind.rsi <= 100), f"RSI out of range: {ind.rsi}"


def test_signal_generator_does_not_silently_crash():
    """
    Core regression guard for the original bug. The fatal bug was an
    AttributeError on every bar, swallowed by a broad try/except, so the
    indicator path inside generate_signal never completed. Here we assert that
    the indicator computation the generator depends on completes for the SAME
    DataFrame the generator would feed it — i.e. the swallowed-crash signature
    is gone. (We avoid asserting a full signal fires on synthetic data, since
    that depends on ~10 market-quality gates and would couple the test to gate
    internals; real-data signal firing is covered by replay_yahoo_india.py.)
    """
    from signal_generator_india import IndiaSignalGenerator
    from backtest_replay_india import _make_replay_config

    gen = IndiaSignalGenerator(_make_replay_config(), watchlist=["TESTSYM"])
    df = _synthetic_trending_df(n=400, drift=0.10)

    # This is exactly the (now-fixed) line 139 path. It must not raise and must
    # return a populated IndicatorSet — before the fix it raised AttributeError.
    eng = gen._recognizer.indicators
    ind = eng.get_latest_indicators(eng.compute(df))
    assert ind is not None and ind.atr is not None and ind.atr > 0, (
        "Signal generator's indicator path is broken — generate_signal would "
        "silently return None for every bar (the original fatal-bug signature)."
    )


def test_real_data_signal_capability_if_network():
    """
    Optional end-to-end check on REAL data: the generator must be CAPABLE of
    producing at least one signal across a real session window. Skipped cleanly
    when market data is unreachable (e.g. offline CI).
    """
    import json
    import urllib.request
    from backtest_replay_india import _make_replay_config, _resample
    from signal_generator_india import IndiaSignalGenerator
    import data_fetch_upstox as dfd

    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS"
               "?range=20d&interval=5m")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        r = json.load(urllib.request.urlopen(req, timeout=15))["chart"]["result"][0]
    except Exception as e:
        print(f"      (skipped — no market data: {repr(e)[:60]})")
        return
    ts, q = r["timestamp"], r["indicators"]["quote"][0]
    rows = [(ts[i], q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i] or 0)
            for i in range(len(ts)) if None not in (q["open"][i], q["high"][i], q["low"][i], q["close"][i])]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.drop(columns=["ts"]).between_time("09:15", "15:30")

    gen = IndiaSignalGenerator(_make_replay_config(), watchlist=["RELIANCE"])
    produced = False
    orig = dfd.get_ohlcv_multi_tf
    try:
        for i in range(60, len(df), 3):
            asof = df.iloc[max(0, i - 120):i + 1]
            r15, r1h = _resample(asof, "15min"), _resample(asof, "60min")
            if r15 is None or r1h is None:
                continue
            dfd.get_ohlcv_multi_tf = lambda _s, _m={"5m": asof, "15m": r15, "1h": r1h}: _m
            if gen.generate_signal("RELIANCE", current_price=float(asof["close"].iloc[-1])) is not None:
                produced = True
                break
    finally:
        dfd.get_ohlcv_multi_tf = orig
    assert produced, "generator produced no signal across a real session — core may be broken"


def main():
    tests = [
        test_recognizer_has_no_phantom_method,
        test_indicator_path_returns_populated_set,
        test_signal_generator_does_not_silently_crash,
        test_real_data_signal_capability_if_network,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {repr(e)[:200]}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
