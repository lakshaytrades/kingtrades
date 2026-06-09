"""
nse_research.py — GOD MODE deep research on the NSE universe ONLY.

Goal: find a strategy that is HIGH ACCURACY and PROFITABLE on Indian stocks
after realistic costs, then report how much it actually pays in rupees.

Tests many strategy families (trend, breakout-confluence, mean-reversion,
pullback) across multiple exit profiles, OOS 2020-2026, after full NSE
intraday costs. Picks the single best survivor and projects monthly INR.
"""
import backtest_strategies as B
import statistics as st, datetime as dt

# -- Expanded liquid NSE universe (more names = more trades = better stats) ----
NSE = ["RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS","SBIN.NS",
       "AXISBANK.NS","LT.NS","ITC.NS","HINDUNILVR.NS","KOTAKBANK.NS","BHARTIARTL.NS",
       "MARUTI.NS","SUNPHARMA.NS","BAJFINANCE.NS","TITAN.NS","ASIANPAINT.NS","WIPRO.NS",
       "TATASTEEL.NS","HCLTECH.NS","ULTRACEMCO.NS","NESTLEIND.NS","POWERGRID.NS",
       "NTPC.NS","TATAMOTORS.NS","ADANIENT.NS","JSWSTEEL.NS","GRASIM.NS","CIPLA.NS",
       "DRREDDY.NS","TECHM.NS","BAJAJFINSV.NS","HINDALCO.NS","COALINDIA.NS","ONGC.NS"]

def cost_rt():
    brok=0.0003*2; stt=0.00025; exch=0.0000297*2; sebi=0.000001*2; stamp=0.00003
    return brok+stt+exch+sebi+stamp+0.18*(brok+exch)+0.0010   # +slippage ~0.21%
COST = cost_rt()

# ── strategy families ─────────────────────────────────────────────────────────
def s_kingedge(b):   # strict trend-confluence breakout
    return (b["e9"]>b["e21"]>b["e50"] and b["adx"]>=25 and b["c"]>=b["hh"]*0.999
            and b["v"]>=2.0*b["vsma"] and 50<=b["rsi"]<72 and b["c"]>b["e50"])
def s_meanrev(b):    # oversold bounce in an uptrend (NSE loves mean reversion)
    return (b["c"]<=b["bbl"] and b["rsi"]<=30 and b["c"]>b["e50"]*0.97)
def s_meanrev_strict(b):  # deeper oversold + must be above long trend
    return (b["c"]<=b["bbl"] and b["rsi"]<=25 and b["c"]>b["e50"] and b["adx"]<35)
def s_pullback_trend(b):  # buy the dip to EMA21 in a confirmed uptrend
    return (b["e21"]>b["e50"] and b["c"]>b["e50"] and b["l"]<=b["e21"]*1.005
            and 42<=b["rsi"]<=56 and b["adx"]>=22)
def s_momentum(b):
    return (b["e9"]>b["e21"]>b["e50"] and 50<=b["rsi"]<=72 and b["c"]>=b["hh"]*0.999
            and b["v"]>=1.5*b["vsma"] and b["adx"]>=20)

STRATS = {"KingEdge":s_kingedge, "MeanRev":s_meanrev, "MeanRevStrict":s_meanrev_strict,
          "PullbackTrend":s_pullback_trend, "Momentum":s_momentum}

# exit profiles to try per strategy
PROFILES = {
    "trend":   {"risk":0.0075,"sl":1.3,"tp":6.0,"ts":30,"trail":True,"partial":True},
    "swing":   {"risk":0.0075,"sl":1.5,"tp":4.0,"ts":40,"trail":True,"partial":True},
    "revert":  {"risk":0.0075,"sl":1.5,"tp":2.0,"ts":8, "trail":False,"partial":False},
}

def run_c(symbols, fn, P):
    B.COST_RT = COST
    return B.run(symbols, {"x":fn}, P)

def score_quality(r):
    """Rank survivors: need >=20 trades, PF>1.1, positive CAGR. Score by Sharpe."""
    if not r or r.get("n",0) < 20: return -999
    if r["pf"] <= 1.10 or r["cagr"] <= 0: return -999
    return r["sharpe"]


def quick_research(symbols=None, capital=500000.0) -> str:
    """
    Compact research callable from Telegram (/research). Uses a smaller liquid
    universe so it finishes in ~60-90s. Returns a formatted verdict string.
    """
    uni = symbols or NSE[:14]   # top liquid names for speed
    rows = []
    for name, fn in STRATS.items():
        best = None
        for pname, P in PROFILES.items():
            r = run_c(uni, fn, P)
            if not r or r.get("n", 0) == 0:
                continue
            if best is None or score_quality(r) > score_quality(best[1]):
                best = (pname, r)
        if best:
            rows.append((name, best[0], best[1], score_quality(best[1])))

    sep = "━" * 28
    out = [f"🔬 NSE RESEARCH (live test)",
           f"{len(uni)} stocks | OOS 2020-26 | cost {COST*100:.2f}%/RT", sep]
    survivors = [x for x in rows if x[3] > -999]
    for name, pname, r, q in sorted(rows, key=lambda x: x[3], reverse=True):
        icon = "🟢" if q > -999 else "🔴"
        out.append(f"{icon} {name}: WR {r['wr']:.0f}% PF {r['pf']:.2f} "
                   f"CAGR {r['cagr']:+.1f}%")
    out.append(sep)
    if not survivors:
        out.append("VERDICT: No daily edge beats costs on NSE.")
        out.append("Trade only live KingEdge setups + watch /proof.")
    else:
        name, pname, r, q = survivors[0]
        monthly = ((1 + r["cagr"]/100)**(1/12) - 1) * 100
        out.append(f"BEST: {name} → {monthly:+.2f}%/mo")
        out.append(f"On Rs.{capital:,.0f}: Rs.{capital*monthly/100:+,.0f}/mo")
    return "\n".join(out)


if __name__ == "__main__":
    print(f"NSE DEEP RESEARCH — {len(NSE)} stocks, OOS 2020-2026, cost {COST*100:.2f}%/RT\n")
    results = []
    for name, fn in STRATS.items():
        best = None
        for pname, P in PROFILES.items():
            r = run_c(NSE, fn, P)
            if not r or r.get("n",0)==0: continue
            if best is None or score_quality(r) > score_quality(best[1]):
                best = (pname, r)
        if best:
            pname, r = best
            q = score_quality(r)
            tag = "🟢 PROFITABLE" if q > -999 else "🔴 loses after costs"
            print(f"  {name:14} [{pname:6}] n={r['n']:4} WR={r['wr']:4.1f}% "
                  f"PF={r['pf']:.2f} Sharpe={r['sharpe']:+.2f} CAGR={r['cagr']:+6.1f}% "
                  f"MDD={r['mdd']:4.1f}%  {tag}")
            results.append((name, pname, r, q))

    survivors = [x for x in results if x[3] > -999]
    print("\n" + "="*70)
    if not survivors:
        print("VERDICT: No strategy is reliably profitable on NSE daily after real")
        print("costs. The honest edge on Indian equities (this universe/timeframe)")
        print("is too thin to overcome costs. Live intraday may differ — /proof decides.")
    else:
        survivors.sort(key=lambda x: x[3], reverse=True)
        name, pname, r, q = survivors[0]
        cap = 500000.0
        monthly = ((1+r["cagr"]/100)**(1/12) - 1) * 100
        print(f"BEST NSE EDGE: {name} [{pname}]")
        print(f"  Win rate {r['wr']:.1f}% | PF {r['pf']:.2f} | Sharpe {r['sharpe']:.2f}")
        print(f"  CAGR {r['cagr']:+.1f}%/yr  ->  ~{monthly:+.2f}%/month")
        print(f"\n  HOW MUCH IT PAYS on Rs.{cap:,.0f} capital:")
        print(f"    Per month:  Rs.{cap*monthly/100:+,.0f}")
        print(f"    Per year:   Rs.{cap*r['cagr']/100:+,.0f}")
        print(f"    Worst drawdown seen: -{r['mdd']:.1f}% (Rs.{cap*r['mdd']/100:,.0f})")
    print("="*70)
