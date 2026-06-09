"""
strategy_research.py — GOD MODE research: build a new edge and test it
HONESTLY with full NSE intraday costs + slippage.

Method:
  1. Realistic all-in cost model (brokerage + STT + exch + SEBI + stamp + GST
     + slippage) — the REAL drag, not an optimistic 0.1%.
  2. Define "KingEdge": trend-confirmed volume breakout with strict confluence
     (the single highest-Sharpe validated edge + filters to cut false signals).
  3. Compare baseline edges vs KingEdge, OOS (2020-2026), US + NSE, AFTER costs.
  4. Report what actually survives. No edge that loses after costs ships.
"""
import backtest_strategies as B
import statistics as st, math, datetime as dt

# ── REALISTIC NSE intraday all-in cost (round-trip, as fraction of turnover) ──
# Brokerage 0.03%x2 (capped style), STT 0.025% sell, exch txn 0.00297%x2,
# SEBI 0.0001%x2, stamp 0.003% buy, GST 18% on (brokerage+exch).
def nse_intraday_cost_rt():
    brok = 0.0003 * 2
    stt  = 0.00025
    exch = 0.0000297 * 2
    sebi = 0.000001 * 2
    stamp= 0.00003
    gst  = 0.18 * (brok + exch)
    return brok + stt + exch + sebi + stamp + gst   # ~0.088%

SLIP_RT = 0.0010   # ~0.05% slippage per side, both sides
COST_REALISTIC = nse_intraday_cost_rt() + SLIP_RT   # ~0.19% round-trip
COST_OPTIMISTIC = 0.0010                            # the old assumption

# ── KingEdge signal: trend-confirmed high-conviction volume breakout ──────────
def s_kingedge(b):
    """
    LONG only when ALL align (confluence cuts false breakouts):
      - Uptrend structure: EMA9 > EMA21 > EMA50  (trend, not chop)
      - Strong trend:      ADX >= 25
      - Fresh 20-bar high breakout: close >= prior 20-bar high
      - Conviction volume: >= 2x average (institutions participating)
      - Not exhausted:     RSI < 72 (room to run)
      - Above 50-EMA:      price > EMA50 (no counter-trend)
    """
    return (b["e9"] > b["e21"] > b["e50"]
            and b["adx"] >= 25
            and b["c"] >= b["hh"] * 0.999
            and b["v"] >= 2.0 * b["vsma"]
            and b["rsi"] < 72
            and b["c"] > b["e50"])

# ── runner with explicit cost override ────────────────────────────────────────
def run_cost(symbols, sigfns, P, cost):
    B.COST_RT = cost
    return B.run(symbols, sigfns, P)

# Exit profiles
P_OLD  = {"risk": 0.0075, "sl": 1.5, "tp": 5.0, "ts": 20, "trail": True,  "partial": True}
# KingEdge exits: tighter SL (cut losers fast), big TP (let winners run), longer hold
P_KING = {"risk": 0.0075, "sl": 1.3, "tp": 6.0, "ts": 30, "trail": True,  "partial": True}

def show(t, r, cost):
    if not r or r.get("n", 0) == 0:
        print(f"  {t:30} no trades"); return
    survive = "🟢 survives" if r["cagr"] > 0 and r["pf"] > 1.10 else "🔴 dies"
    print(f"  {t:30} n={r['n']:4} WR={r['wr']:4.1f}% PF={r['pf']:.2f} "
          f"Sharpe={r['sharpe']:+.2f} CAGR={r['cagr']:+6.1f}% MDD={r['mdd']:4.1f}% "
          f"[cost {cost*100:.2f}%] {survive}")

if __name__ == "__main__":
    print("Loading 15y real data...\n")
    print(f"REALISTIC NSE intraday round-trip cost = {COST_REALISTIC*100:.3f}% "
          f"(vs old optimistic {COST_OPTIMISTIC*100:.2f}%)\n")

    print("===== BASELINE breakout: optimistic vs REALISTIC cost (OOS) =====")
    print("  -- US --")
    show("breakout @ optimistic", run_cost(B.US, {"bo": B.s_breakout_vol}, P_OLD, COST_OPTIMISTIC), COST_OPTIMISTIC)
    show("breakout @ REALISTIC",  run_cost(B.US, {"bo": B.s_breakout_vol}, P_OLD, COST_REALISTIC),  COST_REALISTIC)
    print("  -- NSE --")
    show("breakout @ optimistic", run_cost(B.NSE,{"bo": B.s_breakout_vol}, P_OLD, COST_OPTIMISTIC), COST_OPTIMISTIC)
    show("breakout @ REALISTIC",  run_cost(B.NSE,{"bo": B.s_breakout_vol}, P_OLD, COST_REALISTIC),  COST_REALISTIC)

    print("\n===== NEW: KingEdge (confluence breakout) @ REALISTIC cost (OOS) =====")
    print("  -- US --")
    show("KingEdge US",  run_cost(B.US,  {"king": s_kingedge}, P_KING, COST_REALISTIC), COST_REALISTIC)
    print("  -- NSE (your market) --")
    show("KingEdge NSE", run_cost(B.NSE, {"king": s_kingedge}, P_KING, COST_REALISTIC), COST_REALISTIC)

    print("\n===== KingEdge vs plain breakout, both REALISTIC, US =====")
    show("plain breakout", run_cost(B.US, {"bo": B.s_breakout_vol}, P_KING, COST_REALISTIC), COST_REALISTIC)
    show("KingEdge",       run_cost(B.US, {"king": s_kingedge},     P_KING, COST_REALISTIC), COST_REALISTIC)

    print("\n  ⚠ OOS 2020-2026, daily-bar proxy, AFTER realistic costs.")
    print("    An edge that goes 🔴 here will lose money live. Only 🟢 ships.")
