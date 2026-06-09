"""
risk_frontier.py — Honest answer to "give it everything for 39%/yr".

Takes the single best VALIDATED edge (breakout, OOS Sharpe ~1.0) and sweeps
risk-per-trade to show the REAL trade-off: more return is ONLY bought with
more drawdown. Sharpe is the speed limit; you cannot beat it by wanting more.
"""
import backtest_strategies as B

US = B.US
NSE = B.NSE

def sweep(symbols, label):
    print(f"\n===== {label}: breakout edge, escalating risk/trade (OOS 2020-2026) =====")
    print(f"  {'risk/trade':12} {'CAGR':>8} {'MaxDD':>8} {'Sharpe':>8} {'WR':>6} {'verdict'}")
    for risk in (0.005, 0.01, 0.02, 0.03, 0.05, 0.075):
        P = {"risk": risk, "sl": 1.5, "tp": 5.0, "ts": 20, "trail": True, "partial": True}
        r = B.run(symbols, {"breakout": B.s_breakout_vol}, P)
        if not r or r["n"] == 0:
            print(f"  {risk*100:>5.1f}%       no trades"); continue
        # verdict: a >50% drawdown means practical ruin (psychologically + margin)
        if r["mdd"] >= 50:   v = "💀 RUIN-LEVEL drawdown"
        elif r["mdd"] >= 35: v = "🔴 likely to blow up"
        elif r["mdd"] >= 25: v = "⚠️ very painful"
        else:                v = "🟢 survivable"
        hit = "  <-- ~39% target" if 33 <= r["cagr"] <= 45 else ""
        print(f"  {risk*100:>5.1f}%       {r['cagr']:>+6.1f}% {r['mdd']:>7.1f}% "
              f"{r['sharpe']:>7.2f} {r['wr']:>5.0f}% {v}{hit}")

if __name__ == "__main__":
    print("Loading real 15y data (this is your bot's actual best edge)...")
    sweep(US, "US")
    sweep(NSE, "NSE (your market)")
    print("\n  KEY: Medallion gets 39%/yr at Sharpe ~2.0 (low drawdown).")
    print("       This edge is Sharpe ~1.0, so reaching 39% needs ~2x the risk")
    print("       Medallion takes -> the drawdown is what eats you alive.")
