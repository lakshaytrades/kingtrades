"""
backtest_15yr.py — REAL 15-year backtest on REAL daily candles (Yahoo Finance).

HONEST SCOPE:
  • Real data: ~15y daily OHLCV for a basket of liquid NSE large-caps.
  • Strategy: a faithful DAILY-BAR proxy of the bot's core momentum gates
    (EMA 9/21/50 trend alignment, RSI window, 20-day breakout, volume surge,
    ADX trend) with ATR stop/target and 0.5%-risk sizing.
  • This is NOT the live 5-minute intraday engine — no free source has 15y of
    5m data. Intraday results will differ. This measures whether the MOMENTUM
    EDGE has existed on real Indian equities across 15y of real regimes
    (2008 aftermath, 2013 taper, 2018 NBFC, 2020 COVID, 2022 rate shock).
  • No look-ahead: signal uses data thru close[t], entry at open[t+1].
  • Costs: 0.10% round-trip (brokerage + STT + slippage), applied per trade.
"""
import urllib.request, json, math, datetime as dt
from typing import List, Dict, Optional

BASKET = ["RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS",
          "SBIN.NS","AXISBANK.NS","LT.NS","ITC.NS","HINDUNILVR.NS",
          "KOTAKBANK.NS","BHARTIARTL.NS","MARUTI.NS","TATAMOTORS.NS","SUNPHARMA.NS",
          "BAJFINANCE.NS","TITAN.NS","ASIANPAINT.NS","WIPRO.NS","TATASTEEL.NS"]

RISK_PCT      = 0.005    # 0.5% equity risk per trade
ATR_SL        = 1.5
ATR_TP        = 3.0      # 2:1 reward:risk
MAX_POS       = 5
BREAKOUT_N    = 20
TIME_STOP     = 12       # trading days
COST_RT       = 0.0010   # 0.10% round-trip
START_EQUITY  = 500000.0


def pull(sym: str) -> Optional[dict]:
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=15y&interval=1d"
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"Mozilla/5.0"}), timeout=30)
        res = json.loads(r.read())["chart"]["result"][0]
        ts = res["timestamp"]; q = res["indicators"]["quote"][0]
        bars = []
        for i in range(len(ts)):
            o,h,l,c,v = q["open"][i],q["high"][i],q["low"][i],q["close"][i],q["volume"][i]
            if None in (o,h,l,c,v): continue
            bars.append({"d":dt.date.fromtimestamp(ts[i]),"o":o,"h":h,"l":l,"c":c,"v":v})
        return bars
    except Exception as e:
        print(f"  pull {sym} failed: {e}"); return None


def ema(vals, span):
    k=2/(span+1); out=[vals[0]]
    for x in vals[1:]: out.append(x*k+out[-1]*(1-k))
    return out

def rsi(closes, n=14):
    out=[50.0]*len(closes); gains=[0]; losses=[0]
    for i in range(1,len(closes)):
        ch=closes[i]-closes[i-1]; gains.append(max(ch,0)); losses.append(max(-ch,0))
    ag=al=0
    for i in range(1,len(closes)):
        if i<=n: ag=(ag*(i-1)+gains[i])/i; al=(al*(i-1)+losses[i])/i
        else: ag=(ag*(n-1)+gains[i])/n; al=(al*(n-1)+losses[i])/n
        out[i]=100-100/(1+ag/al) if al>0 else 100.0
    return out

def atr(bars, n=14):
    out=[bars[0]["h"]-bars[0]["l"]]; tr=[out[0]]
    for i in range(1,len(bars)):
        t=max(bars[i]["h"]-bars[i]["l"], abs(bars[i]["h"]-bars[i-1]["c"]), abs(bars[i]["l"]-bars[i-1]["c"]))
        tr.append(t)
    a=tr[0]
    for i in range(1,len(bars)):
        a=(a*(n-1)+tr[i])/n if i>=n else (a*i+tr[i])/(i+1); out.append(a)
    return out

def adx(bars, n=14):
    # simplified ADX
    plus=[0]; minus=[0]; tr=[bars[0]["h"]-bars[0]["l"]]
    for i in range(1,len(bars)):
        up=bars[i]["h"]-bars[i-1]["h"]; dn=bars[i-1]["l"]-bars[i]["l"]
        plus.append(up if (up>dn and up>0) else 0); minus.append(dn if (dn>up and dn>0) else 0)
        tr.append(max(bars[i]["h"]-bars[i]["l"], abs(bars[i]["h"]-bars[i-1]["c"]), abs(bars[i]["l"]-bars[i-1]["c"])))
    def sm(x):
        s=x[0]; o=[s]
        for i in range(1,len(x)): s=s-s/n+x[i]; o.append(s)
        return o
    str_=sm(tr); sp=sm(plus); sm_=sm(minus); out=[0.0]*len(bars); dxs=[]
    for i in range(len(bars)):
        if str_[i]>0:
            pdi=100*sp[i]/str_[i]; mdi=100*sm_[i]/str_[i]
            dx=100*abs(pdi-mdi)/(pdi+mdi) if (pdi+mdi)>0 else 0; dxs.append(dx)
            out[i]=sum(dxs[-n:])/min(len(dxs),n)
    return out


def build(sym):
    bars=pull(sym)
    if not bars or len(bars)<200: return None
    c=[b["c"] for b in bars]
    e9,e21,e50=ema(c,9),ema(c,21),ema(c,50); r=rsi(c); a=atr(bars); ad=adx(bars)
    for i,b in enumerate(bars):
        b["e9"],b["e21"],b["e50"],b["rsi"],b["atr"],b["adx"]=e9[i],e21[i],e50[i],r[i],a[i],ad[i]
        b["hh20"]=max(x["h"] for x in bars[max(0,i-BREAKOUT_N):i]) if i>=BREAKOUT_N else b["h"]
        b["vsma"]=sum(x["v"] for x in bars[max(0,i-20):i])/max(1,min(i,20)) if i>=20 else b["v"]
    return bars

def signal(b):
    return (b["e9"]>b["e21"]>b["e50"] and 50<=b["rsi"]<=72 and
            b["c"]>=b["hh20"]*0.999 and b["v"]>=1.5*b["vsma"] and b["adx"]>=20 and b["atr"]>0)


def backtest():
    print("Pulling 15y real daily data for", len(BASKET), "NSE stocks...")
    data={}
    for s in BASKET:
        d=build(s)
        if d: data[s]=d
    print(f"Loaded {len(data)} symbols.\n")
    if not data: return

    # unified date axis
    all_dates=sorted({b["d"] for d in data.values() for b in d})
    idx={s:{b["d"]:b for b in d} for s,d in data.items()}

    equity=START_EQUITY; peak=equity; maxdd=0.0
    open_pos={}   # sym -> dict
    trades=[]; eq_curve=[]
    for day in all_dates:
        # manage open positions
        for s in list(open_pos.keys()):
            b=idx[s].get(day)
            if not b: continue
            p=open_pos[s]; p["held"]+=1; exit_px=None; reason=None
            if b["l"]<=p["stop"]: exit_px=p["stop"]; reason="stop"      # pessimistic: stop first
            elif b["h"]>=p["tgt"]: exit_px=p["tgt"]; reason="target"
            elif p["held"]>=TIME_STOP: exit_px=b["c"]; reason="time"
            if exit_px is not None:
                gross=p["qty"]*(exit_px-p["entry"])
                cost=COST_RT*p["qty"]*(p["entry"]+exit_px)/2
                pnl=gross-cost; equity+=pnl
                trades.append({"sym":s,"R":pnl/p["risk"] if p["risk"]>0 else 0,"pnl":pnl,
                               "win":pnl>0,"reason":reason,"year":day.year})
                del open_pos[s]
        # new entries (next-day open executed here using today's signal on prev bar)
        if len(open_pos)<MAX_POS:
            for s,d in data.items():
                if s in open_pos or len(open_pos)>=MAX_POS: continue
                arr=d; pos=idx[s].get(day)
                if not pos: continue
                j=None
                # find index of day
                # signal on the PRIOR bar, enter at today's open (no look-ahead)
                # locate prior bar
                # build quick: precomputed sequential — find via list
                # (simple linear position lookup acceptable for daily)
                k=arr.index(pos) if pos in arr else None
                if k is None or k<60: continue
                sg=arr[k-1]
                if signal(sg):
                    entry=pos["o"]; stop=entry-ATR_SL*sg["atr"]; tgt=entry+ATR_TP*sg["atr"]
                    risk_ps=entry-stop
                    if risk_ps<=0: continue
                    qty=(RISK_PCT*equity)/risk_ps
                    open_pos[s]={"entry":entry,"stop":stop,"tgt":tgt,"qty":qty,
                                 "risk":RISK_PCT*equity,"held":0}
        peak=max(peak,equity); dd=(peak-equity)/peak
        maxdd=max(maxdd,dd); eq_curve.append((day,equity))

    # stats
    n=len(trades); wins=[t for t in trades if t["win"]]
    wr=len(wins)/n*100 if n else 0
    avgR=sum(t["R"] for t in trades)/n if n else 0
    avgW=sum(t["R"] for t in wins)/len(wins) if wins else 0
    losers=[t for t in trades if not t["win"]]
    avgL=sum(t["R"] for t in losers)/len(losers) if losers else 0
    pf=(sum(t["pnl"] for t in wins)/abs(sum(t["pnl"] for t in losers))) if losers and sum(t["pnl"] for t in losers)!=0 else float('inf')
    yrs=(all_dates[-1]-all_dates[0]).days/365.25
    cagr=((equity/START_EQUITY)**(1/yrs)-1)*100 if equity>0 else -100
    # daily returns for sharpe
    rets=[]
    for i in range(1,len(eq_curve)):
        p0=eq_curve[i-1][1]; p1=eq_curve[i][1]
        if p0>0: rets.append((p1-p0)/p0)
    import statistics as st
    sharpe=(st.mean(rets)/st.pstdev(rets)*math.sqrt(252)) if len(rets)>2 and st.pstdev(rets)>0 else 0

    print("="*70)
    print("  REAL 15-YEAR BACKTEST — NSE momentum (daily-bar proxy of the bot)")
    print("="*70)
    print(f"  Period           : {all_dates[0]} → {all_dates[-1]}  ({yrs:.1f} yrs)")
    print(f"  Universe         : {len(data)} NSE large-caps | Start capital ₹{START_EQUITY:,.0f}")
    print(f"  Total trades     : {n}   (~{n/yrs:.0f}/yr)")
    print(f"  Win rate         : {wr:.1f}%")
    print(f"  Avg win / loss   : +{avgW:.2f}R / {avgL:.2f}R   (expectancy {avgR:+.3f}R/trade)")
    print(f"  Profit factor    : {pf:.2f}")
    print(f"  Final equity     : ₹{equity:,.0f}")
    print(f"  CAGR             : {cagr:+.1f}% / yr")
    print(f"  Max drawdown     : {maxdd*100:.1f}%")
    print(f"  Sharpe (daily)   : {sharpe:.2f}")
    print("-"*70)
    print("  By year:  trades  WR%   net-R")
    for y in sorted({t["year"] for t in trades}):
        yt=[t for t in trades if t["year"]==y]
        yw=sum(1 for t in yt if t["win"])/len(yt)*100 if yt else 0
        print(f"    {y}     {len(yt):4}   {yw:4.0f}   {sum(t['R'] for t in yt):+6.1f}")
    print("="*70)
    print("  ⚠ Daily-bar proxy on REAL data. NOT the live 5-min intraday result.")
    print("    Intraday adds: more trades, more noise, higher slippage, faster stops.")

if __name__=="__main__":
    backtest()
